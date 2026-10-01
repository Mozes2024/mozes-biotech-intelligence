"""Bounded, offline coverage reconciliation. Findings cannot promote or score."""
import json

from . import db
from .intelligence_store import operation_start, operation_finish
from .promotion import candidate_matches_statement


def audit_coverage(conn, limit=1000):
    rid = operation_start(conn, "coverage_audit")
    rows = conn.execute("SELECT * FROM discovery_candidates ORDER BY discovered_at DESC,candidate_id LIMIT ?", (limit + 1,)).fetchall()
    truncated = len(rows) > limit
    items = []
    for row in rows[:limit]:
        row = dict(row)
        state = db.event_state(conn, row["promoted_event_id"]) if row["promoted_event_id"] else None
        event = conn.execute("SELECT payload FROM events WHERE id=?", (row["promoted_event_id"],)).fetchone() if state else None
        payload = json.loads(event["payload"]) if event else {}
        status = "DISCOVERY_ONLY"
        if not row["ticker"]:
            status = "ENTITY_UNRESOLVED"
        elif state:
            status = "EVENT_DRIVEN_UNRESOLVED" if payload.get("timing_mode") == "EVENT_DRIVEN" and state["status"] in {"VERIFIED", "SCHEDULED"} else "ALREADY_COVERED"
            if state["verification_state"] != "VERIFIED":
                status = "REVIEW_REQUIRED"
        elif row["ticker"]:
            evidence = conn.execute("SELECT payload FROM catalyst_evidence WHERE ticker=? ORDER BY published_at DESC LIMIT 50", (row["ticker"],)).fetchall()
            if any(candidate_matches_statement(row, json.loads(item["payload"]).get("statement", ""))[0] for item in evidence):
                status = "PRIMARY_SOURCE_FOUND_NOT_PROMOTED"
        items.append({"candidate_id": row["candidate_id"], "nct_id": row["nct_id"], "ticker": row["ticker"],
                      "event_id": row["promoted_event_id"], "status": status})
    # Also surface primary statements with no registry candidate at all. This is
    # bounded review evidence, never an implicit promotion or a new scored event.
    evidence_rows = conn.execute("SELECT * FROM catalyst_evidence ORDER BY retrieved_at DESC LIMIT ?", (limit + 1,)).fetchall()
    truncated = truncated or len(evidence_rows) > limit
    for evidence in evidence_rows[:limit]:
        statement = json.loads(evidence["payload"]).get("statement", "")
        matching = [dict(row) for row in rows[:limit] if row["ticker"] == evidence["ticker"]
                    and candidate_matches_statement(dict(row), statement)[0]]
        covered = conn.execute("SELECT 1 FROM event_sources WHERE url=? AND statement=? LIMIT 1",
                               (evidence["source_url"], statement)).fetchone()
        if not matching and not covered:
            items.append({"evidence_id": evidence["evidence_id"], "ticker": evidence["ticker"],
                          "status": "PRIMARY_SOURCE_FOUND_NOT_PROMOTED", "source_url": evidence["source_url"]})
    result = {"last_coverage_audit": db.utcnow(), "candidate_count": min(len(rows), limit),
              "missing_count": sum(x["status"] in {"DISCOVERY_ONLY", "ENTITY_UNRESOLVED", "REVIEW_REQUIRED", "PRIMARY_SOURCE_FOUND_NOT_PROMOTED"} for x in items),
              "review_required_count": sum(x["status"] in {"REVIEW_REQUIRED", "PRIMARY_SOURCE_FOUND_NOT_PROMOTED"} for x in items),
              "unmapped_count": sum(x["status"] == "ENTITY_UNRESOLVED" for x in items),
              "event_driven_count": sum(x["status"] == "EVENT_DRIVEN_UNRESOLVED" for x in items),
              "truncated": truncated, "items": items}
    operation_finish(conn, rid, "PARTIAL" if truncated else "OK", result)
    return result
