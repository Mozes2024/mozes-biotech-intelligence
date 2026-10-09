"""Fail-closed, read-only evidence gates for an explicitly requested stale recovery."""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from .edge_enrichment import edge_request


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


def audit_producers(repo, since, gh, *, now=None):
    """Attempts without checkpoints may be ignored only if they made no new state."""
    now = now or datetime.now(timezone.utc)
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("recovery requires a new run, not a rerun with hidden earlier attempts")
    query = urlencode({"created": f"{since.isoformat()}..{now.isoformat()}", "per_page": 100})
    runs = []
    for page in range(1, 6):
        result = json.loads(gh("api", f"repos/{repo}/actions/workflows/lightweight-monitor.yml/runs?{query}&page={page}"))
        batch = result["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise RuntimeError("recovery audit exceeds 500-run bound; manual reconciliation required")

    def check(run):
        if str(run["id"]) == os.environ.get("GITHUB_RUN_ID") or run["status"] == "queued":
            return None  # The same workflow concurrency lock prevents queued writes.
        if run.get("run_attempt", 1) != 1:
            raise RuntimeError("producer reruns require auditing earlier attempts separately")
        if (run.get("head_branch") != "main" or run.get("name") != "lightweight-live-monitor"
                or (run.get("head_repository") or {}).get("full_name") != repo
                or run["status"] != "completed"):
            raise RuntimeError("untrusted or active producer blocks stale recovery")
        jobs = json.loads(gh("api", f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100"))
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


def verify_durable_history(source, *, trace=edge_request, opener=urllib.request.urlopen):
    """Do not synthesize sent receipts from feed headlines or mutable source data."""
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as conn:
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
