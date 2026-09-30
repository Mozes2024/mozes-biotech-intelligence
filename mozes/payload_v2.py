from __future__ import annotations

from datetime import date, datetime, timezone

from . import db
from .backtest_v2 import report as backtest_report
from .audit import audit_catalog
from .engine_v2 import score_event
from .radar import bootstrap_database, current_resolved_records, live_event_records, validation_status


def build(conn, today: date):
    bootstrap_database(conn)
    t = today.isoformat()
    live = [score_event(conn, e, t) for e in live_event_records(conn, include_quarantined=True)]
    live.sort(key=lambda x: ((x.get("date") or {}).get("window") or {}).get("start") or "9999")
    resolved = current_resolved_records(conn)
    candidates = [dict(r) for r in conn.execute(
        "SELECT candidate_id,nct_id,sponsor,ticker,ticker_confidence,phase,title,primary_completion,last_update_posted,status,promoted_event_id,discovered_at "
        "FROM discovery_candidates ORDER BY primary_completion,candidate_id"
    ).fetchall()]
    return {
        "version": "0.2.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": t,
        "live": live,
        "resolved": resolved,
        "candidates": candidates,
        "validation": validation_status(conn),
        "backtest": backtest_report(conn),
        "historical_audit": audit_catalog(conn),
        "principles": {
            "ctgov_is_discovery_only": True,
            "primary_source_required_for_actionable_date": True,
            "hold_and_runup_empirically_gated": True,
            "readout_and_regulatory_models_separate": True,
        },
    }
