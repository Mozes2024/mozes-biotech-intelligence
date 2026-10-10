"""Fail-closed, read-only evidence gates for an explicitly requested stale recovery."""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from .edge_enrichment import edge_request


class RecoveryConfigurationError(RuntimeError):
    """Report the configuration name, never its value or an HTTP response body."""


def validate_verification_configuration():
    token = os.environ.get("EDGE_SYNC_TOKEN", "")
    if not token.strip() or not token.isascii() or "\r" in token or "\n" in token:
        raise RecoveryConfigurationError("EDGE_SYNC_TOKEN")
    try:
        parts = urlsplit(os.environ.get("MOZES_EDGE_SYNC_URL", ""))
        valid = (parts.scheme == "https" and parts.hostname and not parts.username
                 and not parts.password and parts.port in (None, 443)
                 and not parts.query and not parts.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise RecoveryConfigurationError("MOZES_EDGE_SYNC_URL")


def verification_edge_request(path):
    # A redirect must never carry the control token to another endpoint.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        return edge_request(path, opener=urllib.request.build_opener(NoRedirect()).open)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RecoveryConfigurationError("EDGE_SYNC_TOKEN") from None
        raise RecoveryConfigurationError("MOZES_EDGE_SYNC_URL") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise RecoveryConfigurationError("MOZES_EDGE_SYNC_URL") from None


# Both revisions have the same reviewed enrichment implementation: fetch precedes writes.
REVIEWED_PREFETCH_COMMITS = {
    "116d78d9bce29ef94836a47753c656b59c462871",
    "3d69bf52904fff2a235c1c0ee9321672f2989762",
}

DELIVERY_STEPS = {
    "Reconcile bounded clinical issuer discovery candidates",
    "Hot feeds (FDA, wires, Nasdaq halts, company IR) + push",
    "Hot priority observe + alert outbox",
    "Acknowledge Edge enrichment after durable artifact upload",
    "Acknowledge clinical candidate decisions after durable artifact upload",
    "Publish the small alert feed independently of Pages",
}


def audit_producers(repo, since, gh, *, now=None, full_history=False, source_run_id=None):
    """Attempts without checkpoints may be ignored only if they made no new state."""
    now = now or datetime.now(timezone.utc)
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("recovery requires a new run, not a rerun with hidden earlier attempts")
    # Dispatch time is not queue acquisition time. Stale recovery scans metadata
    # across the entire bounded history, then selects by completion/update time.
    query = urlencode({"per_page": 100} if full_history else
                      {"created": f"{since.isoformat()}..{now.isoformat()}", "per_page": 100})
    runs = []
    for page in range(1, 51 if full_history else 6):
        result = json.loads(gh("api", f"repos/{repo}/actions/workflows/lightweight-monitor.yml/runs?{query}&page={page}"))
        batch = result["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise RuntimeError("producer discovery bound exceeded; manual reconciliation required")
    if full_history:
        selected = []
        for run in runs:
            if str(run["id"]) == str(source_run_id):
                continue  # The reviewed hash-bound checkpoint represents this producer.
            if run["status"] == "completed":
                updated = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    raise RuntimeError("producer completion timestamp missing timezone")
                if updated <= since:
                    continue
            selected.append(run)
        runs = selected
        if len(runs) > 500:
            raise RuntimeError("recovery audit exceeds 500 relevant producers; reconcile manually")

    def check(run):
        if str(run["id"]) == os.environ.get("GITHUB_RUN_ID"):
            return None
        if run.get("run_attempt", 1) != 1:
            raise RuntimeError("producer reruns require auditing earlier attempts separately")
        if (run.get("head_branch") != "main" or run.get("name") != "lightweight-live-monitor"
                or (run.get("head_repository") or {}).get("full_name") != repo
                or run["status"] not in ("completed", "queued", "pending")):
            raise RuntimeError("untrusted or active producer blocks stale recovery")
        jobs = json.loads(gh("api", f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100"))
        if run["status"] in ("queued", "pending"):
            # GitHub uses pending for workflow concurrency waiters. Never infer
            # no writes from that label alone: all returned jobs must be unstarted.
            if (jobs.get("total_count", len(jobs["jobs"])) != len(jobs["jobs"])
                    or any(job.get("status") != "queued" or job.get("started_at")
                           or job.get("completed_at") or job.get("steps") for job in jobs["jobs"])):
                raise RuntimeError("pending producer has started or incomplete job evidence")
            return None
        if jobs.get("total_count", len(jobs["jobs"])) != 1 or len(jobs["jobs"]) != 1:
            raise RuntimeError("incomplete or unexpected producer jobs")
        steps = [step for job in jobs["jobs"] for step in job.get("steps", [])]
        names = {step["name"] for step in steps}
        if not DELIVERY_STEPS.issubset(names):
            raise RuntimeError("producer evidence lacks required delivery steps")
        if not steps or any(step["name"] in DELIVERY_STEPS and step.get("conclusion") != "skipped" for step in steps):
            raise RuntimeError("newer producer may have changed/delivered state; reconcile before recovery")
        enrichment = [s for s in steps if s["name"] == "Enrich the correlated Edge event"]
        if not enrichment:
            raise RuntimeError("producer has no enrichment-step evidence")
        if enrichment[0].get("conclusion") != "skipped":
            if run.get("head_sha") not in REVIEWED_PREFETCH_COMMITS:
                raise RuntimeError("failed enrichment code was not reviewed for pre-write timeout")
            log = gh("run", "view", str(run["id"]), "--repo", repo, "--log-failed")
            if (enrichment[0].get("conclusion") != "failure" or "in fetch_wire" not in log
                    or "TimeoutError:" not in log or "in record_change" in log):
                raise RuntimeError("newer enrichment may have committed state; recovery blocked")
        return str(run["id"])

    with ThreadPoolExecutor(max_workers=3) as pool:
        checked = [run for run in pool.map(check, runs) if run is not None]
    return {"audited_producers": checked, "audited_through": now.isoformat()}


def verify_durable_history(source, *, trace=None, opener=urllib.request.urlopen):
    """Do not synthesize sent receipts from feed headlines or mutable source data."""
    if trace is None:
        validate_verification_configuration()
        trace = verification_edge_request
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        links = conn.execute("SELECT * FROM edge_event_links").fetchall()
        if conn.execute("SELECT COUNT(*) FROM alert_outbox WHERE status IN ('pending','sending','failed')").fetchone()[0]:
            raise RuntimeError("checkpoint has nonterminal delivery state; reconcile provider receipts first")
        for link in links:
            current = trace("event?id=" + link["edge_event_id"])
            if (not current.get("enrichment_ack_at") or current.get("enrichment_change_id") != link["change_id"]):
                raise RuntimeError("Edge completion receipt differs from checkpoint")
        parts = urlsplit(os.environ.get("MOZES_EDGE_SYNC_URL", ""))
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.port not in (None, 443):
            raise RuntimeError("trusted Edge feed configuration required")
        request = urllib.request.Request(f"https://{parts.netloc}/alerts", headers={"User-Agent": "MOZES-LineageAudit/1.0"})
        with opener(request, timeout=15) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise RuntimeError("published feed exceeds recovery bound")
        feed = json.loads(raw)
        if feed.get("schema") != 1 or not isinstance(feed.get("alerts"), list):
            raise RuntimeError("unrecognized published feed")
        for alert in feed["alerts"]:
            change = alert.get("change_id")
            if not conn.execute("SELECT 1 FROM change_events WHERE change_id=?", (change,)).fetchone():
                raise RuntimeError("published change absent from checkpoint; merge trusted newer state first")
            if alert.get("delivered") and not conn.execute("SELECT 1 FROM alert_outbox WHERE change_id=? AND status='sent'", (change,)).fetchone():
                raise RuntimeError("published delivery absent from checkpoint; preserve receipt before recovery")
        return {"verified_edge_completions": len(links), "verified_feed_changes": len(feed["alerts"])}


TENX_EVENT_ID = "EDGE-24534abeca26dcdd08c16db6"
APPROVED_RECOVERY_SHA256 = "f84633088fe4c6fe622682d852a925567118f371e8950f09e9ebaac7898ec853"

def verify_tenx_eligibility(*, trace=None):
    """Dispatch attempts are not completion receipts; reject ambiguous durable writes."""
    event = (trace or verification_edge_request)("event?id=" + TENX_EVENT_ID)
    expected = {"event_id": TENX_EVENT_ID, "edge_event_id": TENX_EVENT_ID,
                "source": "sec", "ticker": "TENX", "accession": "0001193125-26-417939",
                "analysis_status": "complete", "material": 1}
    if not isinstance(event, dict) or any(event.get(k) != v for k, v in expected.items()):
        raise RuntimeError("TENX eligibility: missing or mismatched canonical event")
    if (event.get("lifecycle") == "SUPPRESSED" or event.get("suppression_reason")
            or event.get("enrichment_status") not in ("pending", "awaiting_ack")
            or any(event.get(k) for k in ("enrichment_ack_at", "enrichment_completed_at",
                "enrichment_change_id", "enrichment_github_run_id", "stage0_sent_at"))):
        raise RuntimeError("TENX eligibility: suppressed, completed or ambiguous delivery")
    for key in ("enrichment_alert_ids_json", "enrichment_delivery_json"):
        value = event.get(key)
        if value not in (None, "", "[]"):
            raise RuntimeError("TENX eligibility: conflicting delivery evidence")
    return {"tenx_eligibility": "TENX_ELIGIBLE", "tenx_event_id": TENX_EVENT_ID}
