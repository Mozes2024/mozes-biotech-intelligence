"""Checksum-pinned ATOS display correction; never enqueue, deliver or ACK.

Default CLI mode is read-only. Applying/rolling back a production checkpoint
requires separate operator approval and the reviewed checkpoint SHA256.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .alert_dispatch import build_stage1_payload
from .alert_explanation import explain, normalize_source

CHANGE_ID = "CHG-01557485de654f94415ef482"
EDGE_ID = "EDGE-d221aba82c9b4438bffd21e4"
SOURCE_HASH = "ccaa408dd2adc1964a24a5f4124075e52c5b6989eb3bc5d6d97e2e89f30e6bae"
VERSION = "alert-quality-v3"


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _rows(conn, sql, args=()):
    return [dict(row) for row in conn.execute(sql, args)]


def _history(conn):
    # Full protected-table fingerprints, not just counts; receipts remain byte-for-byte.
    tables = ("change_events", "alert_outbox", "alert_deliveries", "edge_event_links", "source_archive")
    return {table: {"count": len(rows), "sha256": _hash(rows)} for table in tables
            for rows in [_rows(conn, f"SELECT * FROM {table} ORDER BY 1")]}


def prepare(conn):
    link = conn.execute("SELECT * FROM edge_event_links WHERE edge_event_id=?", (EDGE_ID,)).fetchone()
    change = conn.execute("SELECT * FROM change_events WHERE change_id=?", (CHANGE_ID,)).fetchone()
    if not link or link["change_id"] != CHANGE_ID or link["source_hash"] != SOURCE_HASH:
        raise ValueError("canonical Edge receipt mismatch")
    if not change or change["ticker"] != "ATOS" or change["source_hash"] != SOURCE_HASH:
        raise ValueError("canonical change mismatch")
    value = json.loads(change["new_value"])
    if value.get("accession") != "0001193125-26-418560" or change["verification_state"] != "primary_source":
        raise ValueError("canonical accession/verification mismatch")
    source = conn.execute("SELECT * FROM source_archive WHERE source_id=?",
                          (EDGE_ID + "-SRC-" + SOURCE_HASH,)).fetchone()
    if not source or source["canonical_url"] != change["source_url"] or source["content_hash"] != SOURCE_HASH or hashlib.sha256(source["content"].encode()).hexdigest() != SOURCE_HASH:
        raise ValueError("archived source integrity mismatch")
    outbox = _rows(conn, "SELECT * FROM alert_outbox WHERE change_id=? ORDER BY alert_id", (CHANGE_ID,))
    if not outbox or any(row["status"] != "sent" or not row["sent_at"] for row in outbox):
        raise ValueError("unsettled delivery requires reconciliation")
    for row in outbox:
        receipts = _rows(conn, "SELECT * FROM alert_deliveries WHERE alert_id=?", (row["alert_id"],))
        if len(receipts) != 1 or row["attempts"] != 1 or receipts[0]["error_code"]:
            raise ValueError("ambiguous delivery requires reconciliation")
    job = conn.execute("SELECT * FROM alert_analysis_jobs WHERE change_id=?", (CHANGE_ID,)).fetchone()
    if not job or job["status"] != "complete":
        raise ValueError("completed original analysis required")
    prior = conn.execute("SELECT * FROM alert_analyses WHERE analysis_id=?", (job["analysis_id"],)).fetchone()
    if not prior:
        raise ValueError("original analysis missing")
    existing = json.loads(prior["result_json"]).get("display_correction")
    if existing and existing.get("version") == VERSION:
        return {"status": "ALREADY_CORRECTED", "analysis_id": prior["analysis_id"], "history": _history(conn)}
    payload = build_stage1_payload(conn, CHANGE_ID)
    if payload["priority"] != "P2" or payload["outcome"].get("event_outcome") != "conditional_cvr":
        raise ValueError("unexpected corrected classification")
    result = explain(payload, normalize_source(source["content"]), basis="source_text", source_hash=SOURCE_HASH)
    correction = {"version": VERSION, "source_archive_id": source["source_id"],
                  "previous_analysis_id": job["analysis_id"], "previous_job": dict(job),
                  "payload_overrides": {"priority": "P2", "outcome": payload["outcome"]}}
    result.update(display_correction=correction, status="complete")
    analysis_id = "ANA-" + _hash([CHANGE_ID, SOURCE_HASH, VERSION, job["analysis_id"], result])[:24]
    return {"status": "READY_FOR_REVIEW", "change_id": CHANGE_ID, "edge_event_id": EDGE_ID,
            "source_sha256": SOURCE_HASH, "original_job": dict(job), "history": _history(conn),
            "analysis_id": analysis_id, "document_id": prior["document_id"], "result": result}


def apply(conn, plan):
    if conn.in_transaction:
        raise ValueError("correction requires an isolated transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        fresh = prepare(conn)
        if fresh["status"] == "ALREADY_CORRECTED":
            if fresh["analysis_id"] != plan["analysis_id"]:
                raise ValueError("another correction is already selected")
            conn.rollback()
            return fresh
        if fresh != plan:
            raise ValueError("correction plan stale; review a fresh plan")
        stamp = datetime.now(timezone.utc).isoformat()
        result = {**plan["result"], "analyzed_at": stamp}
        conn.execute("INSERT INTO alert_analyses VALUES(?,?,?,?,?,?)",
                     (plan["analysis_id"], CHANGE_ID, plan["document_id"], VERSION, stamp, json.dumps(result, ensure_ascii=False)))
        conn.execute("UPDATE alert_analysis_jobs SET analysis_id=? WHERE change_id=? AND analysis_id=?",
                     (plan["analysis_id"], CHANGE_ID, plan["original_job"]["analysis_id"]))
        if _history(conn) != plan["history"]:
            raise ValueError("protected history changed")
        conn.commit()
        return {"status": "CORRECTED", "analysis_id": plan["analysis_id"], "history": plan["history"]}
    except Exception:
        conn.rollback()
        raise


def rollback(conn, analysis_id):
    if conn.in_transaction:
        raise ValueError("rollback requires an isolated transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT result_json FROM alert_analyses WHERE analysis_id=? AND change_id=?",
                           (analysis_id, CHANGE_ID)).fetchone()
        correction = json.loads(row[0]).get("display_correction") if row else None
        if not correction or correction.get("version") != VERSION:
            raise ValueError("unknown correction")
        job = correction["previous_job"]
        current = conn.execute("SELECT analysis_id FROM alert_analysis_jobs WHERE change_id=?", (CHANGE_ID,)).fetchone()
        if not current or current[0] not in {analysis_id, job["analysis_id"]}:
            raise ValueError("newer analysis selected; rollback refused")
        history = _history(conn)
        conn.execute("UPDATE alert_analysis_jobs SET status=?,attempts=?,retry_after=?,analysis_id=? WHERE change_id=?",
                     (job["status"], job["attempts"], job["retry_after"], job["analysis_id"], CHANGE_ID))
        if history != _history(conn):
            raise ValueError("protected history changed")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply-plan", help="Explicitly apply a separately reviewed plan JSON")
    parser.add_argument("--rollback-analysis")
    parser.add_argument("--expected-db-sha256")
    args = parser.parse_args(argv)
    if args.apply_plan and args.rollback_analysis:
        parser.error("choose apply or rollback")
    path = Path(args.db).resolve()
    writing = bool(args.apply_plan or args.rollback_analysis)
    if writing and (not args.expected_db_sha256 or hashlib.sha256(path.read_bytes()).hexdigest() != args.expected_db_sha256):
        parser.error("reviewed checkpoint SHA256 required and must match")
    with contextlib.closing(sqlite3.connect(path.as_uri() + ("?mode=rw" if writing else "?mode=ro"), uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if args.apply_plan:
            result = apply(conn, json.loads(Path(args.apply_plan).read_text(encoding="utf-8")))
        elif args.rollback_analysis:
            rollback(conn, args.rollback_analysis)
            result = {"status": "ROLLED_BACK"}
        else:
            result = prepare(conn)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
