"""Disabled-by-default October 11 recovery; called inside mozes-hot-monitor only.

No downstream dispatch, D1 mutation, notification or claim retry. The Git ref is
a permanent incident latch, not a lease. A failed claim step also poisons retries
through the existing complete producer/job audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlsplit

import restore_monitor_state as restore
from mozes.monitor_recovery import validate_verification_configuration, verification_edge_request

REPO = "Mozes2024/mozes-biotech-intelligence"
SOURCE = "38052910145"
SHA256 = "0ca9e7f01ccb639914820c96489192d8c5e7842d098e52de0c7fcbb6c19687fb"
START = datetime(2026, 10, 11, tzinfo=timezone.utc)
END = START + timedelta(days=1)
REF = "tags/monitor-recovery-20261011-38052910145"
ACCOUNT = "75b91ca20508a72979cb5337ce1cc7e2"
DATABASE = "9baf99f2-1ccc-4415-affd-2708a7210e7b"
WORKER = "11c60e3e-b948-43db-90af-35e1e86bb56e"
BUNDLE = "13e487d7b85e45c6fc48efa70ce755ca91645c6fe35f6f213feeb5f8d94c01e3"
SETTINGS = "e5fd12e14e3820248e2ff0224543b27b9f85300d05a9fe7eee8ad948fe6cfb00"
PENDING = {
    "EDGE-0355d6c1d3230e9770d460fa": ("ALMS", "globenewswire"),
    "EDGE-3a1f8b2daff5876b5fe398c3": ("VIR", "businesswire"),
    "EDGE-3d3a4fb4e479a3864a65e58b": ("GKOS", "businesswire"),
    "EDGE-7c12c33cf21cf04ab3d56122": ("SGMT", "globenewswire"),
    "EDGE-ad40ba0f5fd5b6363342b9ac": ("GKOS", "businesswire"),
}


def api(path, *, body=None, absent_ok=False):
    """No retries, no provider response bodies/secrets in errors."""
    args = ["gh", "api", f"repos/{REPO}/{path}"]
    if body is not None:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(body) if body else None,
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        if absent_ok and "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError("GitHub request failed/ambiguous; no retry")
    return json.loads(result.stdout)


def cf(path, *, body=None, raw=False):
    token = os.environ.get("CLOUDFLARE_RECOVERY_READ_TOKEN", "")
    if not token.strip():
        raise RuntimeError("CLOUDFLARE_RECOVERY_READ_TOKEN")
    request = urllib.request.Request("https://api.cloudflare.com/client/v4/" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(2097153)
            if len(data) > 2097152:
                raise RuntimeError("Cloudflare evidence exceeds bound")
            if raw:
                return data, response.headers["Content-Type"]
            value = json.loads(data)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError("Cloudflare read evidence unavailable; no retry") from None
    if value.get("errors") or value.get("success") is False:
        raise RuntimeError("Cloudflare read evidence rejected")
    return value.get("result", value)


def provider_gate(now):
    base = f"accounts/{ACCOUNT}/workers/scripts/mozes-hot-clock/"
    settings = cf(base + "settings")
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != SETTINGS:
        raise RuntimeError("Worker settings/bindings drift")
    deployments = cf(base + "deployments")["deployments"]
    latest = max(deployments, key=lambda item: item["created_on"])
    if latest["versions"] != [{"version_id": WORKER, "percentage": 100}]:
        raise RuntimeError("Worker deployment drift")
    if [item["cron"] for item in cf(base + "schedules")["schedules"]] != ["*/2 * * * *"]:
        raise RuntimeError("Worker scheduler drift")
    raw, content_type = cf(base + "content/v2", raw=True)
    message = BytesParser(policy=default).parsebytes(
        ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
    modules = {part.get_filename() or part.get_param("name", header="content-disposition"):
               part.get_payload(decode=True) for part in message.iter_parts()}
    if hashlib.sha256(modules["index.js"]).hexdigest() != BUNDLE:
        raise RuntimeError("Worker bundle drift")
    evidence = cf(f"accounts/{ACCOUNT}/d1/database/{DATABASE}/query", body={"sql":
        "SELECT name FROM d1_migrations ORDER BY id LIMIT 10; "
        "SELECT event_id,enrichment_ack_at,enrichment_change_id FROM edge_events ORDER BY event_id LIMIT 1001;"})
    if (len(evidence) != 2 or any(not item.get("success") or item["meta"]["rows_written"] != 0 for item in evidence)
            or [row["name"] for row in evidence[0]["results"]] != [
                "0001_alert_feed.sql", "0002_hot_edge.sql", "0003_hot_edge_reliability.sql", "0004_clinical_candidates.sql"]):
        raise RuntimeError("D1 schema/read evidence differs")
    events = evidence[1]["results"]
    if len(events) != 27 or sum(bool(row["enrichment_ack_at"]) for row in events) != 8:
        raise RuntimeError("new Edge state requires reconciliation")
    # Read-only provider analytics. A positive CURRENT UTC hour requires an
    # actual production D1 write; midnight itself or successful SELECT is not proof.
    query = ('{viewer{accounts(filter:{accountTag:"' + ACCOUNT + '"}){'
        'd1AnalyticsAdaptiveGroups(limit:100,filter:{date_geq:"2026-10-11",date_leq:"2026-10-11"})'
        '{sum{rowsWritten} dimensions{datetimeHour databaseId}}}}}')
    groups = cf("graphql", body={"query": query})["data"]["viewer"]["accounts"][0]["d1AnalyticsAdaptiveGroups"]
    if len(groups) >= 100:
        raise RuntimeError("analytics completeness bound exhausted")
    total = sum(row["sum"]["rowsWritten"] for row in groups)
    groups = [row for row in groups if row["dimensions"]["databaseId"] == DATABASE]
    hour = now.replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    written = sum(row["sum"]["rowsWritten"] for row in groups if row["dimensions"]["datetimeHour"] == hour)
    if total >= 90000:
        raise RuntimeError("D1 write headroom insufficient")
    return {"capacity_demonstrated": written > 0, "current_hour_rows_written": written,
            "daily_rows_written": total, "worker_version": WORKER}


def context(now):
    approved = os.environ.get("MOZES_INCIDENT_APPROVED_MAIN", "")
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") != "schedule"
            or os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1"
            or not re.fullmatch(r"[a-f0-9]{40}", approved)
            or os.environ.get("GITHUB_SHA") != approved
            or api("commits/main")["sha"] != approved):
        raise RuntimeError("unapproved main/event/rerun")
    if not START <= now < END:
        raise RuntimeError("outside incident execution window")
    runs = api("actions/workflows/ci.yml/runs?head_sha=" + approved + "&per_page=5")["workflow_runs"]
    runs = [run for run in runs if run.get("head_sha") == approved
            and run.get("head_branch") == "main" and run.get("event") == "push"]
    if not runs or runs[0].get("status") != "completed" or runs[0].get("conclusion") != "success":
        raise RuntimeError("approved main CI is not green")


def pending_gate():
    from mozes.edge_enrichment import validate_source
    for event_id, (ticker, source) in PENDING.items():
        event = verification_edge_request("event?id=" + event_id)
        if (event.get("event_id") != event_id or event.get("ticker") != ticker
                or event.get("source") != source or event.get("analysis_status") != "complete"
                or event.get("material") != 1 or event.get("enrichment_ack_at")
                or event.get("enrichment_completed_at") or event.get("enrichment_change_id")
                or event.get("enrichment_delivery_json") or event.get("enrichment_alert_ids_json")):
            raise RuntimeError("pending Edge evidence changed/ambiguous")
        validate_source(event)  # Validate identity/source; never fetch a blocked publisher.


def public_feed():
    validate_verification_configuration()
    host = urlsplit(os.environ["MOZES_EDGE_SYNC_URL"]).netloc
    with urllib.request.urlopen("https://" + host + "/alerts", timeout=15) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise RuntimeError("feed evidence exceeds bound")
    return json.loads(raw)


def preflight(now):
    context(now)
    artifact = api("actions/artifacts/11669812295")
    source = api("actions/runs/" + SOURCE)
    if (artifact.get("expired") is not False
            or str(artifact.get("workflow_run", {}).get("id")) != SOURCE
            or artifact.get("digest") != "sha256:8157f00682d5c415f6fca51e7d6341b714cd9d52fdf5da80b51be6e480f6c447"
            or source.get("run_attempt") != 1 or source.get("conclusion") != "success"):
        raise RuntimeError("trusted checkpoint metadata changed")
    proof = provider_gate(now)
    if not proof["capacity_demonstrated"]:
        return None
    feed = public_feed()
    if (feed.get("revision") != "246b6cd8797afa35a7986eba94f102d1f0975b552bd4af6dfc9b9f529587b73c"
            or len(feed.get("alerts", [])) != 40):
        raise RuntimeError("published alert feed changed")
    pending_gate()
    restore.restore(SOURCE, recovery_sha256=SHA256, verify_only=True, now=now)
    return proof


def ref():
    return api("git/ref/" + REF, absent_ok=True)


def eligible(now):
    # A newer artifact logically closes this incident. Ordinary restore still
    # verifies provenance and MAX_REWIND; never reuse the incident override.
    latest = restore._list_success_runs()
    if not latest:
        raise RuntimeError("missing durable checkpoint")
    if str(latest[0]["databaseId"]) != SOURCE:
        return "ordinary"
    if now < START:
        return "wait"
    if ref() is not None:
        raise RuntimeError("incident claim exists; recovery cannot retry")
    # Existing restore performs the complete producer audit. It also rejects any
    # started prior claim step even when an ambiguous ref creation left no ref.
    return "recover" if preflight(now) else "wait"


def claim(now):
    if eligible(now) != "recover":
        raise RuntimeError("claim eligibility changed")
    # Unreferenced commit creation cannot enable processing. Only confirmed,
    # atomic create-ref succeeds; never PATCH/update/adopt an existing ref.
    head = api("git/commits/" + os.environ["GITHUB_SHA"])
    record = {"incident": SOURCE, "sha256": SHA256,
              "run_id": os.environ["GITHUB_RUN_ID"], "head": os.environ["GITHUB_SHA"]}
    commit = api("git/commits", body={"message": json.dumps(record, sort_keys=True),
                  "tree": head["tree"]["sha"], "parents": [head["sha"]]})
    result = api("git/refs", body={"ref": "refs/" + REF, "sha": commit["sha"]})
    if result.get("ref") != "refs/" + REF or result.get("object", {}).get("sha") != commit["sha"]:
        raise RuntimeError("ambiguous incident claim; stop permanently")
    return commit["sha"]


def process(now):
    if (os.environ.get("MOZES_NTFY_URL") or os.environ.get("MOZES_WEBHOOK_URL")
            or all(os.environ.get(key) for key in
                   ("MOZES_SMTP_USER", "MOZES_SMTP_PASSWORD", "MOZES_ALERT_EMAIL_TO"))):
        raise RuntimeError("external notification configuration enabled")
    value = ref()
    if not value:
        raise RuntimeError("incident claim missing")
    record = json.loads(api("git/commits/" + value["object"]["sha"])["message"])
    if record != {"incident": SOURCE, "sha256": SHA256,
                  "run_id": os.environ["GITHUB_RUN_ID"], "head": os.environ["GITHUB_SHA"]}:
        raise RuntimeError("incident claim belongs to another run")
    if not preflight(now):
        raise RuntimeError("capacity proof lost after claim; no retry")
    # Repeat ALL original restore gates immediately before the first writer step.
    restore.restore(SOURCE, recovery_sha256=SHA256, now=now)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["eligible", "claim", "process"])
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    try:
        if args.mode == "eligible":
            mode = eligible(now)
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write("mode=" + mode + "\n")
            print("INCIDENT_" + mode.upper())
        elif args.mode == "claim":
            claim(now)
            print("INCIDENT_CLAIM_CONFIRMED")
        else:
            process(now)
    except Exception as exc:
        # Never emit payloads, provider bodies or credentials.
        parser.exit(1, "Incident gate stopped: " + type(exc).__name__ + "; no retry\n")


if __name__ == "__main__":
    main()
