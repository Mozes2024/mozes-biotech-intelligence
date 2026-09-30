"""Point-in-time Historical Intelligence Pipeline and readiness gates."""
from __future__ import annotations

import json

from . import db


def readiness_for_case(conn, case_id: str) -> dict:
    case = next((row for row in db.historical_case_rows(conn) if row["case_id"] == case_id), None)
    if not case:
        raise KeyError(case_id)
    snapshots = db.feature_snapshot_rows(conn, case_id)
    labels = db.outcome_label_rows(conn, case_id)
    reasons = []
    if case["legacy_post_hoc"]:
        reasons.append("legacy post-hoc case is quarantined")
    if not snapshots:
        reasons.append("missing point-in-time feature snapshot")
    else:
        snap = snapshots[-1]
        if snap["as_of"] >= case["event_at"]:
            reasons.append("feature snapshot is not strictly before event")
        if not snap["blinded"]:
            reasons.append("feature snapshot was not blinded")
        if not json.loads(snap["provenance_json"] or "[]"):
            reasons.append("feature provenance missing")
    if not labels:
        reasons.append("missing outcome label")
    else:
        label = labels[-1]
        if not label["verified"]:
            reasons.append("outcome label is not verified")
        if not json.loads(label["provenance_json"] or "[]"):
            reasons.append("outcome provenance missing")
    if case["announcement_session"] == "unknown":
        reasons.append("announcement session unknown")
    research_ready = not reasons
    runup_ready = research_ready and case["announcement_session"] != "unknown"
    hold_ready = runup_ready and bool(labels and json.loads(labels[-1]["payload"]).get("event_return") is not None)
    return {"case_id": case_id, "research_ready": research_ready, "runup_ready": runup_ready,
            "hold_ready": hold_ready, "reasons": reasons}


def readiness_summary(conn) -> dict:
    rows = [readiness_for_case(conn, row["case_id"]) for row in db.historical_case_rows(conn)]
    return {"cases": len(rows), "research_ready": sum(r["research_ready"] for r in rows),
            "runup_ready": sum(r["runup_ready"] for r in rows), "hold_ready": sum(r["hold_ready"] for r in rows), "rows": rows}
