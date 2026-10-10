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
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import restore_monitor_state as restore
from mozes.monitor_recovery import RecoveryConfigurationError, validate_verification_configuration, verification_edge_request

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
IDENTITY_FIELDS = ("event_id", "source", "ticker", "cik", "form", "accession", "source_url",
                   "filing_index_url", "first_seen_at", "accepted_at", "published_at")
ACK_FIELDS = ("event_id", "enrichment_ack_at", "enrichment_completed_at", "enrichment_change_id",
              "enrichment_github_run_id", "enrichment_alert_ids_json", "enrichment_delivery_json")
EDGE_FIELDS = tuple(dict.fromkeys(IDENTITY_FIELDS + ACK_FIELDS + ("enrichment_dispatch_at", "analysis_completed_at")))
D1_QUERY = ("SELECT name FROM d1_migrations ORDER BY id LIMIT 10; SELECT " + ",".join(EDGE_FIELDS)
            + " FROM edge_events ORDER BY event_id LIMIT 1001;")
# Deterministic projections of protected audited evidence, never database contents.
EDGE_FINGERPRINTS = {
    "event_ids": "5dee66a02bb3b020ab67ad2f1930a1e0fd6d57eb0ffa15914d2d4a17597f79e1",
    "identities": "e5db31f6fb06a63f1fca07c1aea8be09a7f5fecc49963167c23e8aeffe2752d7",
    "completed_acks": "6038406ec20f4ebc38fdd380733f62e85b9114478f9fd4557b994facd5c392fb",
}
PENDING = {
    "EDGE-0355d6c1d3230e9770d460fa": ("ALMS", "globenewswire"),
    "EDGE-3a1f8b2daff5876b5fe398c3": ("VIR", "businesswire"),
    "EDGE-3d3a4fb4e479a3864a65e58b": ("GKOS", "businesswire"),
    "EDGE-7c12c33cf21cf04ab3d56122": ("SGMT", "globenewswire"),
    "EDGE-ad40ba0f5fd5b6363342b9ac": ("GKOS", "businesswire"),
}


class IncidentGateError(RuntimeError):
    """Static, safe gate reason; never a provider response or event payload."""


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise IncidentGateError("missing/invalid timezone-aware provider timestamp") from None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def edge_fingerprints(events):
    if not isinstance(events, list) or len(events) != 27:
        raise IncidentGateError("Edge identity evidence incomplete")
    identities, acks, ids = [], [], []
    for event in events:
        if not isinstance(event, dict) or not set(EDGE_FIELDS).issubset(event):
            raise IncidentGateError("Edge identity/correlation fields missing")
        edge_id = event["event_id"]
        if not isinstance(edge_id, str) or not re.fullmatch(r"EDGE-[a-f0-9]{24}", edge_id):
            raise IncidentGateError("invalid canonical Edge identity")
        ids.append(edge_id)
        identity = {field: event[field] for field in IDENTITY_FIELDS}
        identity["first_seen_at"] = timestamp(identity["first_seen_at"]).isoformat()
        identities.append(identity)
        if event["enrichment_ack_at"]:
            ack = {field: event[field] for field in ACK_FIELDS}
            if not re.fullmatch(r"CHG-[a-f0-9]{24}", ack["enrichment_change_id"] or ""):
                raise IncidentGateError("completed ACK/change correlation missing")
            for field in ("enrichment_ack_at", "enrichment_completed_at"):
                ack[field] = timestamp(ack[field]).isoformat()
            for field in ("enrichment_alert_ids_json", "enrichment_delivery_json"):
                try:
                    ack[field] = json.loads(ack[field])
                    if not isinstance(ack[field], list):
                        raise ValueError()
                except (TypeError, ValueError):
                    raise IncidentGateError("completed ACK receipt evidence invalid") from None
            acks.append(ack)
        elif any(event[field] for field in ACK_FIELDS[2:]):
            raise IncidentGateError("uncompleted Edge event has ambiguous correlations")
    if len(set(ids)) != 27 or len(acks) != 8:
        raise IncidentGateError("Edge identity/ACK set differs")
    return {"event_ids": fingerprint(sorted(ids)),
            "identities": fingerprint(sorted(identities, key=lambda row: row["event_id"])),
            "completed_acks": fingerprint(sorted(acks, key=lambda row: row["event_id"]))}


def analytics_query(now):
    day = now.astimezone(timezone.utc).date().isoformat()
    return ('{viewer{accounts(filter:{accountTag:"' + ACCOUNT + '"}){'
        'd1AnalyticsAdaptiveGroups(limit:100,filter:{date_geq:"' + day + '",date_leq:"' + day + '"})'
        '{sum{rowsWritten} dimensions{datetimeHour databaseId}}}}}')


def analytics_evidence(value, now, events):
    try:
        accounts = value["data"]["viewer"]["accounts"]
        if value.get("errors") or not isinstance(accounts, list) or len(accounts) != 1:
            raise ValueError()
        groups = accounts[0]["d1AnalyticsAdaptiveGroups"]
        if not isinstance(groups, list) or not groups or len(groups) >= 100:
            raise ValueError()
        total, written, seen = 0, 0, set()
        hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        for row in groups:
            stamp = timestamp(row["dimensions"]["datetimeHour"])
            database = row["dimensions"]["databaseId"]
            count = row["sum"]["rowsWritten"]
            if (not isinstance(database, str) or not database or (database, stamp) in seen
                    or stamp.date() != hour.date() or stamp > hour
                    or stamp.minute or stamp.second or stamp.microsecond
                    or type(count) not in (int, float) or count < 0 or count != int(count)):
                raise ValueError()
            seen.add((database, stamp))
            total += int(count)
            if database == DATABASE and stamp == hour:
                written += int(count)
        if (DATABASE, hour) not in seen:
            raise IncidentGateError("current-hour provider evidence missing/stale")
        if total >= 90000:
            raise IncidentGateError("D1 write headroom insufficient")
        # Hourly aggregation alone cannot prove a write was recent. Require a
        # persisted automatic write timestamp too; never manufacture a probe.
        writes = [timestamp(event[field]) for event in events
                  for field in ("enrichment_dispatch_at", "analysis_completed_at") if event[field]]
        recent = [stamp for stamp in writes if max(START, now - timedelta(minutes=10)) <= stamp <= now]
        return {"capacity_demonstrated": written > 0 and bool(recent),
                "current_hour_rows_written": written, "daily_rows_written": total,
                "latest_automatic_write_at": max(recent).isoformat() if recent else None,
                "worker_version": WORKER}
    except (KeyError, TypeError, ValueError, OverflowError):
        raise IncidentGateError("provider analytics incomplete/invalid") from None


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
        raise IncidentGateError("GitHub request failed/ambiguous; no retry")
    return json.loads(result.stdout)


def cf(path, *, body=None, raw=False):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    token = os.environ.get("CLOUDFLARE_RECOVERY_READ_TOKEN", "")
    if not token.strip():
        raise IncidentGateError("CLOUDFLARE_RECOVERY_READ_TOKEN")
    base = f"accounts/{ACCOUNT}/workers/scripts/mozes-hot-clock/"
    get_paths = {base + suffix for suffix in ("settings", "deployments", "schedules", "content/v2")}
    get_paths.add(f"accounts/{ACCOUNT}/d1/database/{DATABASE}")
    query_path = f"accounts/{ACCOUNT}/d1/database/{DATABASE}/query"
    if not ((path in get_paths and body is None)
            or (path == query_path and body == {"sql": D1_QUERY})
            or (path == "graphql" and isinstance(body, dict) and set(body) == {"query"})):
        raise IncidentGateError("non-read-only provider request rejected")
    request = urllib.request.Request("https://api.cloudflare.com/client/v4/" + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            server_time = parsedate_to_datetime(response.headers.get("Date", ""))
            if server_time.tzinfo is None or abs((datetime.now(timezone.utc) - server_time).total_seconds()) > 120:
                raise IncidentGateError("provider response timestamp missing/stale")
            data = response.read(2097153)
            if len(data) > 2097152:
                raise IncidentGateError("Cloudflare evidence exceeds bound")
            if raw:
                return data, response.headers["Content-Type"]
            value = json.loads(data)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise IncidentGateError("Cloudflare read evidence unavailable; no retry") from None
    if not isinstance(value, dict) or value.get("errors"):
        raise IncidentGateError("Cloudflare read evidence rejected")
    if path == "graphql":
        if not isinstance(value.get("data"), dict):
            raise IncidentGateError("Cloudflare analytics data missing")
        return value
    if value.get("success") is not True or "result" not in value or value["result"] is None:
        raise IncidentGateError("Cloudflare REST envelope incomplete")
    return value["result"]


def provider_gate(now):
    try:
        return _provider_gate(now)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise IncidentGateError("provider response shape incomplete/invalid") from None


def _provider_gate(now):
    base = f"accounts/{ACCOUNT}/workers/scripts/mozes-hot-clock/"
    settings = cf(base + "settings")
    digest = hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != SETTINGS:
        raise IncidentGateError("Worker settings/bindings drift")
    deployments = cf(base + "deployments")["deployments"]
    if not isinstance(deployments, list) or not deployments:
        raise IncidentGateError("Worker deployment evidence missing")
    dates = [timestamp(item["created_on"]) for item in deployments]
    if dates != sorted(dates, reverse=True) or dates[0] > now:
        raise IncidentGateError("Worker deployment evidence invalid")
    latest = deployments[0]  # Cloudflare documents first = actively serving deployment.
    if latest["versions"] != [{"version_id": WORKER, "percentage": 100}]:
        raise IncidentGateError("Worker deployment drift")
    if [item["cron"] for item in cf(base + "schedules")["schedules"]] != ["*/2 * * * *"]:
        raise IncidentGateError("Worker scheduler drift")
    raw, content_type = cf(base + "content/v2", raw=True)
    message = BytesParser(policy=default).parsebytes(
        ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
    if not message.is_multipart():
        raise IncidentGateError("Worker module response invalid")
    modules = {part.get_filename() or part.get_param("name", header="content-disposition"):
               part.get_payload(decode=True) for part in message.iter_parts()}
    if list(modules) != ["index.js"] or len(list(message.iter_parts())) != 1:
        raise IncidentGateError("Worker module set changed/incomplete")
    if hashlib.sha256(modules["index.js"]).hexdigest() != BUNDLE:
        raise IncidentGateError("Worker bundle drift")
    metadata = cf(f"accounts/{ACCOUNT}/d1/database/{DATABASE}")
    if (metadata.get("uuid") != DATABASE or metadata.get("name") != "mozes-alerts"
            or timestamp(metadata.get("created_at")) > now):
        raise IncidentGateError("D1 database metadata identity differs")
    evidence = cf(f"accounts/{ACCOUNT}/d1/database/{DATABASE}/query", body={"sql": D1_QUERY})
    if (len(evidence) != 2 or any(not item.get("success") or item["meta"]["rows_written"] != 0 for item in evidence)
            or [row["name"] for row in evidence[0]["results"]] != [
                "0001_alert_feed.sql", "0002_hot_edge.sql", "0003_hot_edge_reliability.sql", "0004_clinical_candidates.sql"]):
        raise IncidentGateError("D1 schema/read evidence differs")
    events = evidence[1]["results"]
    actual_fingerprints = edge_fingerprints(events)
    if actual_fingerprints != EDGE_FINGERPRINTS:
        raise IncidentGateError("Edge identities/ACK correlations differ from audited baseline")
    # Read-only provider analytics. A positive CURRENT UTC hour requires an
    # actual production D1 write; midnight itself or successful SELECT is not proof.
    proof = analytics_evidence(cf("graphql", body={"query": analytics_query(now)}), now, events)
    proof["edge_fingerprints"] = actual_fingerprints
    return proof


def context(now):
    approved = os.environ.get("MOZES_INCIDENT_APPROVED_MAIN", "")
    if (os.environ.get("GITHUB_REPOSITORY") != REPO
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") != "schedule"
            or os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1"
            or not re.fullmatch(r"[a-f0-9]{40}", approved)
            or os.environ.get("GITHUB_SHA") != approved
            or api("commits/main")["sha"] != approved):
        raise IncidentGateError("unapproved main/event/rerun")
    if not START <= now < END:
        raise IncidentGateError("outside incident execution window")
    runs = api("actions/workflows/ci.yml/runs?head_sha=" + approved + "&per_page=5")["workflow_runs"]
    runs = [run for run in runs if run.get("head_sha") == approved
            and run.get("head_branch") == "main" and run.get("event") == "push"]
    if not runs or runs[0].get("status") != "completed" or runs[0].get("conclusion") != "success":
        raise IncidentGateError("approved main CI is not green")


def pending_gate():
    from mozes.edge_enrichment import validate_source
    for event_id, (ticker, source) in PENDING.items():
        event = verification_edge_request("event?id=" + event_id)
        if (event.get("event_id") != event_id or event.get("ticker") != ticker
                or event.get("source") != source or event.get("analysis_status") != "complete"
                or event.get("material") != 1 or event.get("enrichment_ack_at")
                or event.get("enrichment_completed_at") or event.get("enrichment_change_id")
                or event.get("enrichment_delivery_json") or event.get("enrichment_alert_ids_json")):
            raise IncidentGateError("pending Edge evidence changed/ambiguous")
        validate_source(event)  # Validate identity/source; never fetch a blocked publisher.


def public_feed():
    validate_verification_configuration()
    host = urlsplit(os.environ["MOZES_EDGE_SYNC_URL"]).netloc
    with urllib.request.urlopen("https://" + host + "/alerts", timeout=15) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise IncidentGateError("feed evidence exceeds bound")
    return json.loads(raw)


def preflight(now):
    context(now)
    artifact = api("actions/artifacts/11669812295")
    source = api("actions/runs/" + SOURCE)
    if (artifact.get("expired") is not False
            or str(artifact.get("workflow_run", {}).get("id")) != SOURCE
            or artifact.get("digest") != "sha256:8157f00682d5c415f6fca51e7d6341b714cd9d52fdf5da80b51be6e480f6c447"
            or source.get("run_attempt") != 1 or source.get("conclusion") != "success"):
        raise IncidentGateError("trusted checkpoint metadata changed")
    proof = provider_gate(now)
    if not proof["capacity_demonstrated"]:
        return None
    feed = public_feed()
    if (feed.get("revision") != "246b6cd8797afa35a7986eba94f102d1f0975b552bd4af6dfc9b9f529587b73c"
            or len(feed.get("alerts", [])) != 40):
        raise IncidentGateError("published alert feed changed")
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
        raise IncidentGateError("missing durable checkpoint")
    if str(latest[0]["databaseId"]) != SOURCE:
        return "ordinary"
    if now < START:
        return "wait"
    if ref() is not None:
        raise IncidentGateError("incident claim exists; recovery cannot retry")
    # Existing restore performs the complete producer audit. It also rejects any
    # started prior claim step even when an ambiguous ref creation left no ref.
    return "recover" if preflight(now) else "wait"


def claim(now):
    if eligible(now) != "recover":
        raise IncidentGateError("claim eligibility changed")
    # Unreferenced commit creation cannot enable processing. Only confirmed,
    # atomic create-ref succeeds; never PATCH/update/adopt an existing ref.
    head = api("git/commits/" + os.environ["GITHUB_SHA"])
    record = {"incident": SOURCE, "sha256": SHA256,
              "run_id": os.environ["GITHUB_RUN_ID"], "head": os.environ["GITHUB_SHA"]}
    commit = api("git/commits", body={"message": json.dumps(record, sort_keys=True),
                  "tree": head["tree"]["sha"], "parents": [head["sha"]]})
    result = api("git/refs", body={"ref": "refs/" + REF, "sha": commit["sha"]})
    if result.get("ref") != "refs/" + REF or result.get("object", {}).get("sha") != commit["sha"]:
        raise IncidentGateError("ambiguous incident claim; stop permanently")
    return commit["sha"]


def process(now):
    if (os.environ.get("MOZES_NTFY_URL") or os.environ.get("MOZES_WEBHOOK_URL")
            or all(os.environ.get(key) for key in
                   ("MOZES_SMTP_USER", "MOZES_SMTP_PASSWORD", "MOZES_ALERT_EMAIL_TO"))):
        raise IncidentGateError("external notification configuration enabled")
    value = ref()
    if not value:
        raise IncidentGateError("incident claim missing")
    record = json.loads(api("git/commits/" + value["object"]["sha"])["message"])
    if record != {"incident": SOURCE, "sha256": SHA256,
                  "run_id": os.environ["GITHUB_RUN_ID"], "head": os.environ["GITHUB_SHA"]}:
        raise IncidentGateError("incident claim belongs to another run")
    provider_proof = preflight(now)
    if not provider_proof:
        raise IncidentGateError("capacity proof lost after claim; no retry")
    # Repeat ALL original restore gates immediately before the first writer step.
    restore.restore(SOURCE, recovery_sha256=SHA256, now=now)
    path = Path(os.environ.get("MOZES_DB_PATH", ".monitor/mozes-live.db")).with_name("recovery-proof.json")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["incident_claim"] = {**record, "commit": value["object"]["sha"],
                                  "provider": provider_proof, "verified_at": now.isoformat()}
    path.write_text(json.dumps(evidence), encoding="utf-8")


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
        reason = str(exc) if isinstance(exc, (IncidentGateError, RecoveryConfigurationError)) else type(exc).__name__
        parser.exit(1, "Incident gate stopped: " + reason + "; no retry\n")


if __name__ == "__main__":
    main()
