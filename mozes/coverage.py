"""Bounded, offline coverage reconciliation. Findings cannot promote or score."""
import json

from . import db
from .intelligence_store import operation_start, operation_finish
from .promotion import candidate_matches_statement


def audit_coverage(conn, limit=None):
    rid = operation_start(conn, "coverage_audit")
    total_candidates = conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0]
    total_evidence = conn.execute("SELECT COUNT(*) FROM catalyst_evidence").fetchone()[0]
    query_limit = (limit + 1) if limit is not None else None
    sql = "SELECT * FROM discovery_candidates ORDER BY discovered_at DESC,candidate_id"
    rows = conn.execute(sql + (" LIMIT ?" if query_limit else ""), (query_limit,) if query_limit else ()).fetchall()
    truncated = limit is not None and len(rows) > limit
    if truncated:
        rows = rows[:limit]
    items = []
    issuer_queue = {r['ticker']: dict(r) for r in conn.execute(
        'SELECT ticker,state,failure_reason FROM v2e2_issuer_queue').fetchall()}
    for row in rows[:limit]:
        row = dict(row)
        mapping = (json.loads(row.get("raw_json") or "{}").get("_mapping") or {})
        state = db.event_state(conn, row["promoted_event_id"]) if row["promoted_event_id"] else None
        event = conn.execute("SELECT payload FROM events WHERE id=?", (row["promoted_event_id"],)).fetchone() if state else None
        payload = json.loads(event["payload"]) if event else {}
        status = "DISCOVERY_ONLY"
        if not row["ticker"]:
            status = "ENTITY_AMBIGUOUS" if mapping.get("basis") == "ambiguous_collaborators" else "ENTITY_UNRESOLVED"
        elif state:
            status = "EVENT_DRIVEN_UNRESOLVED" if payload.get("timing_mode") == "EVENT_DRIVEN" and state["status"] in {"VERIFIED", "SCHEDULED"} else "ALREADY_COVERED"
            if state["verification_state"] != "VERIFIED":
                status = "REVIEW_REQUIRED"
        elif row["ticker"]:
            evidence = conn.execute("SELECT payload FROM catalyst_evidence WHERE ticker=? ORDER BY published_at DESC LIMIT 50", (row["ticker"],)).fetchall()
            if any(candidate_matches_statement(row, json.loads(item["payload"]).get("statement", ""))[0] for item in evidence):
                status = "PRIMARY_SOURCE_FOUND_NOT_PROMOTED"
        queue = issuer_queue.get(row['ticker']) or {}
        reason = ('multiple_public_collaborators' if status == 'ENTITY_AMBIGUOUS' else
                  'no_confident_issuer_mapping' if status == 'ENTITY_UNRESOLVED' else
                  'awaiting_bounded_primary_source_scan' if status == 'DISCOVERY_ONLY' and queue.get('state') == 'PENDING' else
                  'no_matching_primary_source' if status == 'DISCOVERY_ONLY' and queue.get('state') == 'SUCCESS' else
                  queue.get('failure_reason') or None)
        items.append({"candidate_id": row["candidate_id"], "nct_id": row["nct_id"], "ticker": row["ticker"],
                      "event_id": row["promoted_event_id"], "status": status,
                      "issuer_queue_state": queue.get('state'), "disposition_reason": reason,
                      "mapping_basis": mapping.get("basis"),
                      "matched_issuers": mapping.get("matched_issuers") or []})
    # Also surface primary statements with no registry candidate at all. This is
    # bounded review evidence, never an implicit promotion or a new scored event.
    evidence_sql = "SELECT * FROM catalyst_evidence ORDER BY retrieved_at DESC"
    evidence_rows = conn.execute(evidence_sql + (" LIMIT ?" if query_limit else ""), (query_limit,) if query_limit else ()).fetchall()
    truncated = truncated or (limit is not None and len(evidence_rows) > limit)
    if limit is not None:
        evidence_rows = evidence_rows[:limit]
    for evidence in evidence_rows:
        statement = json.loads(evidence["payload"]).get("statement", "")
        matching = [dict(row) for row in rows[:limit] if row["ticker"] == evidence["ticker"]
                    and candidate_matches_statement(dict(row), statement)[0]]
        covered = conn.execute("SELECT 1 FROM event_sources WHERE url=? AND statement=? LIMIT 1",
                               (evidence["source_url"], statement)).fetchone()
        if not matching and not covered:
            items.append({"evidence_id": evidence["evidence_id"], "ticker": evidence["ticker"],
                          "status": "PRIMARY_SOURCE_FOUND_NOT_PROMOTED", "source_url": evidence["source_url"]})
    result = {"last_coverage_audit": db.utcnow(), "candidate_count": len(rows),
              "total_candidates": total_candidates, "scanned_candidates": len(rows),
              "total_evidence": total_evidence, "scanned_evidence": len(evidence_rows),
              "missing_count": sum(x["status"] in {"DISCOVERY_ONLY", "ENTITY_UNRESOLVED", "ENTITY_AMBIGUOUS", "REVIEW_REQUIRED", "PRIMARY_SOURCE_FOUND_NOT_PROMOTED"} for x in items),
              "review_required_count": sum(x["status"] in {"REVIEW_REQUIRED", "PRIMARY_SOURCE_FOUND_NOT_PROMOTED"} for x in items),
              "unmapped_count": sum(x["status"] in {"ENTITY_UNRESOLVED", "ENTITY_AMBIGUOUS"} for x in items),
              "event_driven_count": sum(x["status"] == "EVENT_DRIVEN_UNRESOLVED" for x in items),
              "truncated": truncated, "stop_reason": "row_cap" if truncated else "complete", "items": items}
    operation_finish(conn, rid, "INCOMPLETE" if truncated else "OK", result)
    return result
