from __future__ import annotations

import json
import os
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
from .financial_context import enrich_financial_context, operation_health

VERSION = "0.3.1"


def _decode_details(row):
    if not row:
        return None
    out = dict(row)
    raw = out.pop("details_json", "{}")
    try:
        out["details"] = json.loads(raw or "{}")
    except Exception:
        out["details"] = {}
    return out


def _refresh_status(conn):
    out = _decode_details(conn.execute(
        "SELECT run_id,started_at,finished_at,status,details_json FROM refresh_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone())
    if out and out.get('status') == 'RUNNING':
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(out['started_at'])).total_seconds()
        except (TypeError, ValueError):
            age = 10**9
        out['run_state'] = 'ACTIVE_LEASE' if age <= 120 else 'ORPHANED'
    return out


def _monitor_status(conn):
    return _decode_details(conn.execute(
        "SELECT run_id,started_at,finished_at,status,details_json FROM monitor_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone())


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
    market_attention = sum(1 for x in live if ((x.get("market_intelligence") or {}).get("attention_score") or 0) >= 50)
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


def _apply_public_research_label_guard(rows, validation):
    """Keep public labels aligned with locked empirical gates.

    The heuristic engine may still compute strong setups internally, but until at least one
    empirical gate is open the public payload uses research-priority language rather than
    an investment-candidate label.
    """
    gates_open = bool(validation["runup"].get("satisfied") or validation["hold_through"].get("satisfied"))
    if gates_open:
        return rows
    for row in rows:
        rec = row.get("recommendation") or {}
        status = rec.get("status")
        if status == "INVESTMENT_CANDIDATE":
            rec.update({
                "status": "HIGH_RESEARCH_PRIORITY",
                "label_he": "עדיפות מחקר גבוהה",
                "why_he": "האירוע בולט לפי ההיוריסטיקה, אך שערי האימות האמפיריים עדיין נעולים.",
                "missing_he": "אימות OOS / walk-forward לפני שימוש בתווית השקעתית.",
            })
        elif status == "APPROACHING_CANDIDATE":
            rec.update({
                "status": "RESEARCH_WORTHY",
                "label_he": "שווה מחקר",
                "why_he": "האירוע נראה מבטיח לפי ההיוריסטיקה, אך טרם הוכח יתרון אמפירי.",
                "missing_he": "עוד ראיות ו-validation מחוץ למדגם.",
            })
        row["recommendation"] = rec
    return rows


def _health(conn, live, refresh, monitor):
    stale_prices = []
    fresh_prices = []
    for row in live:
        mi = row.get("market_intelligence") or {}
        if not mi.get("available"):
            continue
        target = {"ticker": row.get("ticker"), "date": mi.get("price_date"), "age_days": mi.get("price_age_days")}
        if mi.get("price_fresh"):
            fresh_prices.append(target)
        else:
            stale_prices.append(target)
    # one row per ticker for concise health reporting
    def uniq(rows):
        out, seen = [], set()
        for r in rows:
            if r["ticker"] in seen:
                continue
            seen.add(r["ticker"]); out.append(r)
        return out
    stale_prices, fresh_prices = uniq(stale_prices), uniq(fresh_prices)
    warnings = []
    red, amber, info = [], [], []
    operations = operation_health(conn).get("modules", {})
    for name, op in operations.items():
        status = op.get("status")
        if status in {"PARTIAL", "FAILED"}:
            red.append({"module": name, "status": status, "details": op.get("details") or {}})
        elif status == "RUNNING":
            target = amber if op.get("run_state") == "ACTIVE_LEASE" else red
            target.append({"module": name, "status": op.get("run_state", "ORPHANED"), "details": op.get("details") or {}})
        elif status in {"INCOMPLETE", "BOUNDED"}:
            amber.append({"module": name, "status": status, "details": op.get("details") or {}})
        elif status == "SKIPPED":
            info.append({"module": name, "status": status, "details": op.get("details") or {}})
    if not os.environ.get("SEC_USER_AGENT"):
        info.append({"module": "identity", "status": "SKIPPED", "reason": "SEC monitoring identity is not configured"})
    for name, run in (("monitor", monitor), ("refresh", refresh)):
        if not run or run.get("status") in {"OK"}:
            continue
        target = red if run.get("status") in {"PARTIAL", "FAILED"} or run.get('run_state') == 'ORPHANED' else amber
        target.append({"module": name, "status": run.get("status"), "details": run})
    if stale_prices:
        amber.append({"module": "prices", "status": "INCOMPLETE", "reason": "stale prices", "count": len(stale_prices)})
    warnings.extend([f"{x['module']} collection is {x['status'].lower()}" for x in red + amber])
    catalyst_healthy = not any(x["module"] in {"monitor", "refresh", "deep_refresh"} for x in red)
    return {
        "status": "ok" if not red and not amber else "degraded",
        "warnings": warnings,
        "severity": {"red": red, "amber": amber, "info": info},
        "primary_catalyst_monitoring": {"healthy": catalyst_healthy,
                                         "label_he": "ניטור אירועי הליבה תקין" if catalyst_healthy else "ניטור אירועי הליבה דורש בדיקה"},
        "coverage": (operations.get("coverage_audit") or {}).get("details"),
        "sec_monitoring_enabled": bool(os.environ.get("SEC_USER_AGENT")),
        "latest_monitor": monitor,
        "latest_refresh": refresh,
        "fresh_price_tickers": fresh_prices,
        "stale_price_tickers": stale_prices,
    }


def build(conn, today: date):
    bootstrap_database(conn)
    t = today.isoformat()
    validation = validation_status(conn)
    scored = [score_event(conn, e, t) for e in live_event_records(conn, include_quarantined=True)]
    _apply_public_research_label_guard(scored, validation)
    enrich_live_rows(conn, scored, today)
    enrich_financial_context(conn, scored, today)
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
    paper = PaperBook(conn).list()
    historical = readiness_summary(conn)
    refresh = _refresh_status(conn)
    monitor = _monitor_status(conn)
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
        "refresh": refresh,
        "changes": recent_changes(conn),
        "health": _health(conn, live, refresh, monitor),
        "intelligence_v2c": operation_health(conn),
        "validation": validation,
        "coverage": (operation_health(conn).get("modules", {}).get("coverage_audit") or {}).get("details"),
        "paper_integrity": PaperBook(conn).verify(),
        "forward_candidates": [dict(row) for row in conn.execute("SELECT candidate_key,event_id,first_observed_at FROM forward_candidates ORDER BY first_observed_at DESC LIMIT 100")],
        "backtest": backtest_report(conn),
        "historical_audit": audit_catalog(conn),
        "principles": {
            "ctgov_is_discovery_only": True,
            "primary_source_required_for_actionable_date": True,
            "hold_and_runup_empirically_gated": True,
            "readout_and_regulatory_models_separate": True,
            "scores_are_not_probabilities": True,
            "market_attention_is_observational_only": True,
            "investment_candidate_label_requires_open_empirical_gate": True,
        },
    }
