from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timezone

from . import db
from .audit import audit_catalog
from .backtest_v2 import report as backtest_report
from .engine_v2 import score_event
from .paper import PaperBook
from .radar import bootstrap_database, current_resolved_records, live_event_records, validation_status
from .historical import readiness_summary
from .security import tradability
from .live_monitor import recent_changes
from .live_intelligence import enrich_live_rows

VERSION = "0.3.0"


def _refresh_status(conn):
    row = conn.execute(
        "SELECT run_id,started_at,finished_at,status,details_json FROM refresh_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    try:
        out["details"] = json.loads(out.pop("details_json") or "{}")
    except Exception:
        out["details"] = {}
    return out


def _watch_universe(conn):
    return [{**dict(r), "security": tradability(conn, r["ticker"])} for r in conn.execute(
        "SELECT ticker,company,cik,source,active,updated_at FROM watch_universe WHERE active=1 ORDER BY ticker"
    ).fetchall()]


def _summary(conn, live, stale, resolved, candidates, validation, paper, historical):
    classes = Counter((x.get("classification") or {}).get("class", "UNKNOWN") for x in live)
    states = Counter((x.get("state") or {}).get("status", "UNKNOWN") for x in live)
    verified = sum(1 for x in live if (x.get("state") or {}).get("verification_state") == "VERIFIED")
    quarantined = sum(1 for x in live if (x.get("classification") or {}).get("class") == "QUARANTINED")
    high_impact = sum(1 for x in live if (x.get("impact") or {}).get("score", 0) >= 70)
    next_30 = sum(1 for x in live if x.get("days_to") is not None and 0 <= x["days_to"] <= 30)
    mapped = sum(1 for c in candidates if c.get("ticker"))
    market_attention = sum(1 for x in live if (x.get("market_intelligence") or {}).get("attention_score", 0) >= 50)
    recent_shocks = sum(1 for x in live if (x.get("market_intelligence") or {}).get("recent_shock"))
    chase_flags = sum(1 for x in live if (x.get("market_intelligence") or {}).get("post_event_chase_risk"))
    return {
        "live_events": len(live),
        "stale_events": len(stale),
        "tradable_verified_tickers": sum(tradability(conn, row["ticker"])["tradable"] for row in db.watch_rows(conn)),
        "unknown_tradability_tickers": sum(tradability(conn, row["ticker"])["status"] == "UNKNOWN" for row in db.watch_rows(conn)),
        "primary_verified_live": verified,
        "historical_cases": historical["cases"],
        "historical_research_ready": historical["research_ready"],
        "historical_runup_ready": historical["runup_ready"],
        "historical_hold_ready": historical["hold_ready"],
        "market_attention_high": market_attention,
        "recent_market_shocks": recent_shocks,
        "post_event_chase_flags": chase_flags,
        "verified": verified,
        "quarantined": quarantined,
        "high_impact": high_impact,
        "next_30_days": next_30,
        "resolved_events": len(resolved),
        "candidates": len(candidates),
        "mapped_candidates": mapped,
        "paper_signals": len(paper),
        "class_counts": dict(classes),
        "state_counts": dict(states),
        "runup_gate": validation["runup"],
        "hold_gate": validation["hold_through"],
    }


def build(conn, today: date):
    bootstrap_database(conn)
    t = today.isoformat()
    scored = [score_event(conn, e, t) for e in live_event_records(conn, include_quarantined=True)]
    enrich_live_rows(conn, scored, today)
    stale = [row for row in scored if row["classification"]["class"] == "STALE_UNRESOLVED"]
    for row in stale:
        window = (row.get("date") or {}).get("window") or {}
        row["stale_reason"] = row["classification"]["reasons"][0]
        row["age_days"] = (today - date.fromisoformat(window["start"])).days if window.get("start") else None
    live = [row for row in scored if row["classification"]["class"] != "STALE_UNRESOLVED"]
    live.sort(key=lambda x: (((x.get("date") or {}).get("window") or {}).get("start") or "9999", -(x.get("impact") or {}).get("score", 0)))
    resolved = current_resolved_records(conn)
    resolved.sort(key=lambda x: ((x.get("state") or {}).get("resolved_at") or "", x.get("ticker") or ""), reverse=True)
    candidates = [dict(r) for r in conn.execute(
        "SELECT candidate_id,nct_id,sponsor,ticker,ticker_confidence,phase,title,primary_completion,last_update_posted,status,promoted_event_id,discovered_at "
        "FROM discovery_candidates ORDER BY primary_completion,candidate_id"
    ).fetchall()]
    validation = validation_status(conn)
    paper = PaperBook(conn).list()
    historical = readiness_summary(conn)
    return {
        "version": VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": t,
        "summary": _summary(conn, live, stale, resolved, candidates, validation, paper, historical),
        "live": live,
        "stale": stale,
        "resolved": resolved,
        "candidates": candidates,
        "paper": paper,
        "watch_universe": _watch_universe(conn),
        "refresh": _refresh_status(conn),
        "changes": recent_changes(conn),
        "validation": validation,
        "backtest": backtest_report(conn),
        "historical_audit": audit_catalog(conn),
        "principles": {
            "ctgov_is_discovery_only": True,
            "primary_source_required_for_actionable_date": True,
            "hold_and_runup_empirically_gated": True,
            "readout_and_regulatory_models_separate": True,
            "scores_are_not_probabilities": True,
            "market_attention_is_observational_only": True,
        },
    }
