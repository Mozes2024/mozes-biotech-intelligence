"""Historical-data eligibility audit for model validation."""
from __future__ import annotations

from . import db


def audit_event(conn, event: dict, outcome: dict | None = None) -> dict:
    reasons = []
    annotation = (event.get("annotation") or "").lower()
    if "post-hoc" in annotation or "memory" in annotation or "hindsight" in annotation:
        reasons.append("features not blinded / reconstructed post hoc")
    if not event.get("features_as_of"):
        reasons.append("features_as_of missing")
    outcome = outcome or {}
    if outcome.get("verified") is not True:
        reasons.append("outcome/move not fully verified")
    sources = db.load_event_sources(conn, event["id"])
    if not sources and not event.get("documents") and not event.get("chronology"):
        reasons.append("no pre-event provenance")
    prices = db.load_prices(conn, event.get("ticker"), before=event.get("date")) if event.get("ticker") else []
    if len(prices) < 31:
        reasons.append("insufficient pre-event price history")
    state = db.event_state(conn, event["id"]) or {}
    if state.get("event_session", "unknown") == "unknown":
        reasons.append("announcement session unknown")
    return {"id": event["id"], "eligible_for_validation": not reasons, "reasons": reasons}


def audit_catalog(conn):
    outcomes = db.load_outcomes(conn)
    rows = [audit_event(conn, e, outcomes.get(e["id"])) for e in db.load_events(conn, "historical")]
    return {
        "n": len(rows),
        "eligible": sum(r["eligible_for_validation"] for r in rows),
        "ineligible": sum(not r["eligible_for_validation"] for r in rows),
        "rows": rows,
    }
