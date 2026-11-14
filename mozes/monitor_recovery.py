"""Fail-closed, read-only evidence gates for an explicitly requested stale recovery."""
from __future__ import annotations

import json
import os
import sqlite3
import time
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


def verify_historical_incident(repo, run, jobs, gh):
    """Recognize one reviewed pre-claim failure, never a reusable SHA exemption."""
    head = "ba458d6cab5a97b38d923b4f8bb60ee19ce9377d"
    if (repo != "Mozes2024/mozes-biotech-intelligence"
            or str(run.get("id")) != "38133079950" or run.get("head_sha") != head
            or run.get("status") != "completed" or run.get("conclusion") != "failure"
            or run.get("event") != "workflow_dispatch" or run.get("run_attempt") != 1):
        raise RuntimeError("unreviewed historical incident provenance")
    gate = next(job for job in jobs if job.get("name") == "incident-eligibility")
    monitor = next((job for job in jobs if job.get("name") == "monitor"), None)
    steps = gate.get("steps", [])
    expected_steps = [
        ("Set up job", "success"), ("Run actions/checkout@v4", "success"),
        ("Run actions/setup-python@v5", "success"), ("Run pip install -e .", "success"),
        ("Verify incident recovery eligibility", "failure"),
        ("Acquire irreversible incident recovery claim", "skipped"),
        ("Post Run actions/setup-python@v5", "skipped"),
        ("Post Run actions/checkout@v4", "success"), ("Complete job", "success"),
    ]
    if (gate.get("status") != "completed" or gate.get("conclusion") != "failure"
            or [(s.get("name"), s.get("conclusion")) for s in steps] != expected_steps
            or any(s.get("status") != "completed" for s in steps)
            or monitor is None or monitor.get("status") != "completed"
            or monitor.get("conclusion") != "skipped" or monitor.get("steps") != []):
        raise RuntimeError("historical incident may have processed state")
    expected = {
        ".github/workflows/lightweight-monitor.yml": "d8b2561dcdb6dd018d233b77b7376f4e55da19fe",
        "scripts/incident_recovery.py": "bf1bb33535bdd420c2770130436c6cbebda9d260",
        "scripts/restore_monitor_state.py": "5bffd761854b8e7ab01b64a51504c954c6d52331",
        "mozes/monitor_recovery.py": "864863aa29fa649d761cb5346faf247b951a17c9",
    }
    ancestry = json.loads(gh("api", f"repos/{repo}/compare/{head}...main"))
    if (ancestry.get("status") not in ("ahead", "identical")
            or ancestry.get("merge_base_commit", {}).get("sha") != head):
        raise RuntimeError("historical incident is not trusted main ancestry")
    for path, blob in expected.items():
        evidence = json.loads(gh("api", f"repos/{repo}/contents/{path}?ref={head}"))
        if evidence.get("type") != "file" or evidence.get("path") != path or evidence.get("sha") != blob:
            raise RuntimeError("historical incident code provenance changed or incomplete")


# Resource ceilings are failure boundaries, never evidence of complete history.
PRODUCER_AUDIT_SECONDS = 240
PRODUCER_INVENTORY_MAX_PAGES = 1000


def _complete_producer_inventory(repo, gh):
    """Read every unfiltered page, including an empty terminal page if needed.

    The caller bounds each request and total elapsed time. Counts, identities and
    exact page sizes must agree; a short page alone is not proof of completeness.
    A second inventory after job validation detects observed concurrent changes.
    """
    path = f"repos/{repo}/actions/workflows/lightweight-monitor.yml/runs?per_page=100"

    def page(number, expected=None):
        result = json.loads(gh("api", f"{path}&page={number}"))
        if not isinstance(result, dict):
            raise RuntimeError("incomplete producer inventory")
        count, rows = result.get("total_count"), result.get("workflow_runs")
        if (type(count) is not int or count < 0 or not isinstance(rows, list)
                or (expected is not None and count != expected)):
            raise RuntimeError("incomplete or changed producer inventory")
        if len(rows) != min(100, max(0, count - (number - 1) * 100)):
            raise RuntimeError("incomplete producer inventory page")
        for run in rows:
            if (not isinstance(run, dict) or type(run.get("id")) is not int
                    or run["id"] <= 0 or not isinstance(run.get("status"), str)):
                raise RuntimeError("malformed producer inventory metadata")
            try:
                updated = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
                if updated.tzinfo is None:
                    raise ValueError
            except (KeyError, AttributeError, TypeError, ValueError):
                raise RuntimeError("producer completion timestamp missing or invalid timezone") from None
        return count, rows

    expected, rows = page(1)
    # Always observe a genuine terminal page, even when count is exactly full.
    terminal = expected // 100 + 1
    if terminal > PRODUCER_INVENTORY_MAX_PAGES:
        raise RuntimeError("producer inventory resource bound exceeded; manual reconciliation required")
    with ThreadPoolExecutor(max_workers=4) as pool:
        for _, batch in pool.map(lambda n: page(n, expected), range(2, terminal + 1)):
            rows.extend(batch)
    if len(rows) != expected or len({r["id"] for r in rows}) != expected:
        raise RuntimeError("incomplete or duplicate producer inventory")
    return rows


def _inventory_signature(runs):
    # Compare all provider metadata, not just total_count or newest created_at.
    return {r["id"]: json.dumps(r, sort_keys=True, separators=(",", ":")) for r in runs}


def audit_producers(repo, since, gh, *, now=None, full_history=False, source_run_id=None):
    """Attempts without checkpoints may be ignored only if they made no new state."""
    now = now or datetime.now(timezone.utc)
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("recovery requires a new run, not a rerun with hidden earlier attempts")
    # Dispatch time is not queue acquisition time. Never filter full history by
    # created_at: an old-created producer may complete after the checkpoint.
    inventory_before = None
    if full_history:
        deadline = time.monotonic() + PRODUCER_AUDIT_SECONDS
        runner = gh

        def gh(*args):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("producer audit time budget exceeded")
            value = runner(*args, timeout=min(30, remaining))
            if time.monotonic() >= deadline:
                raise RuntimeError("producer audit time budget exceeded")
            return value

        runs = _complete_producer_inventory(repo, gh)
        inventory_before = _inventory_signature(runs)
    else:
        # Preserve the existing non-stale discovery path and policy unchanged.
        query = urlencode({"created": f"{since.isoformat()}..{now.isoformat()}", "per_page": 100})
        runs = []
        for page in range(1, 6):
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
                    or any((job.get("status") != "queued" or job.get("started_at")
                            or job.get("completed_at") or job.get("steps"))
                           and not (job.get("name") in {"incident-eligibility", "prospective-pilot-claim", "prospective-pilot-cleanup", "nov14-checkpoint-only"}
                                    and job.get("conclusion") == "skipped" and not job.get("steps"))
                           for job in jobs["jobs"])):
                raise RuntimeError("pending producer has started or incomplete job evidence")
            return None
        if jobs.get("total_count", len(jobs["jobs"])) != len(jobs["jobs"]):
            raise RuntimeError("incomplete or unexpected producer jobs")
        monitor_jobs = jobs["jobs"]
        prospective = [job for job in monitor_jobs if job.get("name") in
                       {"prospective-pilot-claim", "prospective-pilot-cleanup", "nov14-checkpoint-only"}]
        if (len({job.get("name") for job in prospective}) != len(prospective)
                or any(job.get("conclusion") != "skipped" or job.get("steps") for job in prospective)):
            raise RuntimeError("prospective claim requires manual recovery audit")
        monitor_jobs = [job for job in monitor_jobs if job not in prospective]
        gates = [job for job in monitor_jobs if job.get("name") == "incident-eligibility"]
        if gates:
            if len(gates) != 1 or len(monitor_jobs) != 2:
                raise RuntimeError("incomplete incident job evidence")
            gate_steps = gates[0].get("steps", [])
            claims = [s for s in gate_steps if s.get("name") == "Acquire irreversible incident recovery claim"]
            disabled = gates[0].get("conclusion") == "skipped" and not gate_steps
            if not disabled and (len(claims) != 1 or claims[0].get("conclusion") != "skipped"):
                # Includes crashes, ambiguous HTTP failures and successful claims.
                # An absent ref cannot authorize a second attempt.
                raise RuntimeError("prior incident claim attempted; no automatic recovery retry")
            if not disabled:
                if not any(step.get("name") == "Verify incident recovery eligibility" for step in gate_steps):
                    raise RuntimeError("unreviewed or incomplete incident eligibility evidence")
                if str(run["id"]) == "38133079950":
                    verify_historical_incident(repo, run, monitor_jobs, gh)
                elif (not os.environ.get("MOZES_INCIDENT_APPROVED_MAIN")
                      or run.get("head_sha") != os.environ["MOZES_INCIDENT_APPROVED_MAIN"]):
                    raise RuntimeError("unreviewed or incomplete incident eligibility evidence")
            monitor_jobs = [job for job in monitor_jobs if job is not gates[0]]
            if monitor_jobs[0].get("conclusion") == "skipped" and not monitor_jobs[0].get("steps"):
                return str(run["id"])
        if len(monitor_jobs) != 1:
            raise RuntimeError("incomplete or unexpected producer jobs")
        steps = [step for job in monitor_jobs for step in job.get("steps", [])]
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
    if full_history:
        inventory_after = _inventory_signature(_complete_producer_inventory(repo, gh))
        if time.monotonic() >= deadline:
            raise RuntimeError("producer audit time budget exceeded")
        if inventory_before != inventory_after:
            raise RuntimeError("producer inventory changed during audit; reconcile before recovery")
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
