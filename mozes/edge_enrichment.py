"""Exact-source enrichment and ACK after the workflow saves the monitor artifact.

Completion means source analysis + CHG/outbox decision are durable, not that a
private delivery channel succeeded. Those statuses accompany the private ACK.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from . import db
from .config import DB_PATH
from .ingest.edgar import _get, html_to_text
from .ingest.edgar_latest import biotech_universe
from .live_monitor import record_change
from .materiality import classify_outcome
from .primary_feeds import canonical_url, USER_AGENT


def edge_request(path, *, payload=None, opener=urllib.request.urlopen):
    sync_url = os.environ.get("MOZES_EDGE_SYNC_URL", "")
    token = os.environ.get("EDGE_SYNC_TOKEN", "")
    parts = urlsplit(sync_url)
    if parts.scheme != "https" or not parts.hostname or not token:
        raise RuntimeError("edge control configuration incomplete")
    url = f"https://{parts.netloc}/edge/{path}"
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "MOZES-EdgeSync/1.0"})
    with opener(request, timeout=15) as response:
        limit=262144 if path=='candidates' else 65536
        raw=response.read(limit+1)
        if len(raw)>limit:raise ValueError('Edge control response too large')
        return json.loads(raw)


def validate_source(event):
    parts = urlsplit(event["source_url"])
    allowed = {"sec": {"www.sec.gov"}, "businesswire": {"www.businesswire.com", "businesswire.com"},
               "globenewswire": {"www.globenewswire.com", "rss.globenewswire.com"}}
    # Official wire RSS can supply HTTP links; never fetch plaintext.
    if event["source"] != "sec" and parts.scheme == "http" and parts.port is None:
        parts = parts._replace(scheme="https")
    if parts.scheme != "https" or parts.hostname not in allowed.get(event["source"], set()) or parts.username or parts.port not in (None, 443):
        raise ValueError("untrusted Edge source URL")
    if event["source"] == "sec":
        accession = event.get("accession") or ""
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession) or not parts.path.startswith(
                f"/Archives/edgar/data/{int(event['cik'])}/{accession.replace('-', '')}/"):
            raise ValueError("SEC source does not match accession and CIK")
    return parts.geturl()


def process(conn, event, *, fetch=None, github_run_id=None):
    edge_id = event.get("edge_event_id") or event.get("event_id")
    if not re.fullmatch(r"EDGE-[a-f0-9]{24}", edge_id or "") or event.get("analysis_status") != "complete" or not event.get("material"):
        raise ValueError("Edge event not ready for enrichment")
    secure_url = validate_source(event)
    run_id = github_run_id or os.environ.get("GITHUB_RUN_ID", "")
    if not re.fullmatch(r"\d{1,20}", run_id):
        raise ValueError("GitHub run ID required for durable trace")
    ticker = event["ticker"]
    issuer = biotech_universe(conn).get(str(int(event["cik"])))
    if not issuer or issuer["ticker"] != ticker:
        raise ValueError("Edge issuer no longer uniquely verified in universe")
    for env_key, field in [("MOZES_HOT_TICKER", "ticker"), ("MOZES_HOT_CIK", "cik"),
                           ("MOZES_HOT_ACCESSION", "accession"), ("MOZES_HOT_SOURCE_URL", "source_url")]:
        supplied = os.environ.get(env_key)
        if supplied and supplied != str(event.get(field) or ""):
            raise ValueError("workflow input differs from canonical Edge event")
    event = {**event, "source_url": secure_url}
    prior = conn.execute("SELECT change_id FROM edge_event_links WHERE edge_event_id=?", (edge_id,)).fetchone()
    if prior:
        return prior[0]
    if fetch is None:
        def fetch_wire(url):
            from .wire_transport import fetch_wire as fetch_exact_wire, fetch_with_cooldown
            return fetch_with_cooldown(conn, url, fetch=lambda target: fetch_exact_wire(
                target, user_agent=USER_AGENT,
                validate_redirect=lambda redirect: validate_source({**event, "source_url": redirect})))
        fetch = _get if event["source"] == "sec" else fetch_wire
    raw = fetch(event["source_url"])
    content = html_to_text(raw)
    if not content.strip():
        raise ValueError("empty primary source document")
    outcome = classify_outcome(content)
    from .clinical_events import classify_clinical
    outcome = classify_clinical(content,outcome)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    archive_id = edge_id + "-SRC-" + digest
    if not conn.execute("SELECT 1 FROM source_archive WHERE source_id=?", (archive_id,)).fetchone():
        db.archive_source(conn, archive_id, event["source_url"], event["source"],
                          event.get("published_at") or event.get("accepted_at"), db.utcnow(), raw,
                          {"edge_event_id": edge_id})
    sec = event["source"] == "sec"
    value = {"headline": content[:300] if sec else event["headline"], "summary": content[:8000], "outcome": outcome,
             "form": event.get("form"), "accession": event.get("accession"), "accepted": event.get("accepted_at"),
             "published_at": event.get("published_at")}
    # Preserve existing canonical CHG identities and priority rules.
    identity = ["sec_latest", event["accession"]] if sec else ["wire", canonical_url(event["source_url"])]
    change_id = record_change(conn, ticker=ticker, change_type="sec_material_filing" if sec else "wire_release_signal",
        previous_value=None, new_value=value, source_url=event["source_url"], source_type="sec" if sec else "wire",
        severity="high" if outcome.get("material") else "medium", verification_state="primary_source" if sec else "investigation_only",
        source_hash=digest, identity=identity, metadata={"edge_event_id": edge_id, "cik": event["cik"],
            "source_published_at": event.get("published_at"), "accepted_at": event.get("accepted_at")})
    with conn:
        conn.execute("INSERT OR IGNORE INTO edge_event_links VALUES(?,?,?,?,?)", (edge_id, change_id, run_id, db.utcnow(), digest))
        primary = conn.execute("SELECT primary_change_id FROM alert_links WHERE change_id=?", (change_id,)).fetchone()
        alert_change = primary[0] if primary else change_id
        for row in conn.execute("SELECT alert_id,payload_json FROM alert_outbox WHERE change_id=?", (alert_change,)).fetchall():
            payload = json.loads(row["payload_json"])
            payload["edge_event_ids"] = sorted(set(payload.get("edge_event_ids", []) + [edge_id]))
            conn.execute("UPDATE alert_outbox SET payload_json=? WHERE alert_id=?", (json.dumps(payload), row["alert_id"]))
    return change_id


def ack_payload(conn, edge_id, *, github_run_id=None):
    receipt = conn.execute("SELECT * FROM edge_event_links WHERE edge_event_id=?", (edge_id,)).fetchone()
    if not receipt:
        raise RuntimeError("no durable Edge processing receipt")
    change_id = receipt["change_id"]
    primary = conn.execute("SELECT primary_change_id FROM alert_links WHERE change_id=?", (change_id,)).fetchone()
    alert_change = primary[0] if primary else change_id
    deliveries = [dict(row) for row in conn.execute("SELECT alert_id,status,sent_at FROM alert_outbox WHERE change_id=?", (alert_change,))]
    # A detection whose enqueue failed is not successful until a retry records a decision.
    if not deliveries and not conn.execute("SELECT 1 FROM alert_enqueue_decisions WHERE change_id=?", (change_id,)).fetchone():
        raise RuntimeError("Edge change has no durable outbox decision")
    change=conn.execute('SELECT new_value FROM change_events WHERE change_id=?',(change_id,)).fetchone()
    value=json.loads(change[0]) if change else {}
    return {"edge_event_id": edge_id, "status": "completed", "change_id": change_id,
            "catalyst_id": value.get('catalyst_id') if isinstance(value,dict) else None,
            "github_run_id": github_run_id or os.environ.get("GITHUB_RUN_ID") or receipt["github_run_id"],
            "completed_at": db.utcnow(), "alert_ids": [row["alert_id"] for row in deliveries], "delivery": deliveries}


def checkpoint(path):
    path = Path(path)
    value = {"schema": 1, "producer_run_id": os.environ["GITHUB_RUN_ID"], "created_at": db.utcnow(),
             "db_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    path.with_name("edge-checkpoint.json").write_text(json.dumps(value), encoding="utf-8")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("process", "checkpoint", "ack"))
    args = parser.parse_args()
    path = os.environ.get("MOZES_DB_PATH", DB_PATH)
    if args.action == "checkpoint":
        print(json.dumps(checkpoint(path)))
        return
    edge_id = os.environ.get("MOZES_EDGE_EVENT_ID")
    if not edge_id:
        print(json.dumps({"status": "SKIPPED", "reason": "no Edge event"}))
        return
    if args.action == "ack":
        receipt = json.loads(Path(path).with_name("edge-checkpoint.json").read_text(encoding="utf-8"))
        if receipt.get("producer_run_id") != os.environ.get("GITHUB_RUN_ID") or receipt.get("db_sha256") != hashlib.sha256(Path(path).read_bytes()).hexdigest():
            raise RuntimeError("Edge ACK requires the uploaded immutable checkpoint")
    conn = db.connect(path)
    try:
        if args.action == "process":
            event = edge_request("event?id=" + edge_id)
            change_id = process(conn, event)
            print(json.dumps({"edge_event_id": edge_id, "change_id": change_id, "status": "processed"}))
        else:
            print(json.dumps(edge_request("ack", payload=ack_payload(conn, edge_id))))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
