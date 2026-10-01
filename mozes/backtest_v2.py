"""Point-in-time backtesting v0.2.

Reports every run-up grid, uses calendar-aligned benchmarks, and uses announcement session
for hold-through returns. No strategy gate is unlocked from in-sample results.
"""
from __future__ import annotations

import json
from statistics import mean, median, stdev

from . import db
from .historical import attached_price_rows, readiness_for_case
from .market import event_return, join_on_dates

RUNUP_GRID = [(entry, exit_) for entry in (60,45,30,21,14,7) for exit_ in (14,7,3,1) if exit_ < entry]


def stats(xs):
    xs = list(xs)
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs), "mean": mean(xs), "median": median(xs),
        "win_rate": sum(x > 0 for x in xs) / len(xs),
        "sd": stdev(xs) if len(xs) > 1 else 0.0,
        "worst": min(xs), "best": max(xs),
    }


def _window_return(rows, entry, exit_):
    if len(rows) <= entry:
        return None
    # T-1 is last row. T-k uses k trading sessions before event reference.
    a = rows[-1-entry]
    b = rows[-1-exit_]
    return a, b, b["close"] / a["close"] - 1


def runup_one(stock, bench, entry, exit_):
    sr = _window_return(stock, entry, exit_)
    if not sr:
        return None
    a, b, r = sr
    pairs = {s["date"]: (s, x) for s, x in join_on_dates(stock, bench)}
    ex = None
    if a["date"] in pairs and b["date"] in pairs:
        sa, ba = pairs[a["date"]]
        sb, bb = pairs[b["date"]]
        ex = r - (bb["close"] / ba["close"] - 1)
    return {"from": a["date"], "to": b["date"], "return": r, "excess": ex}


def all_runup_grids(event_rows):
    """event_rows: [{stock:[...], benchmark:[...]}]. Report every parameter combination."""
    out = []
    for entry, exit_ in RUNUP_GRID:
        rows = []
        for e in event_rows:
            r = runup_one(e.get("stock", []), e.get("benchmark", []), entry, exit_)
            if r:
                rows.append(r)
        out.append({
            "entry": entry, "exit": exit_,
            "return": stats(x["return"] for x in rows),
            "excess": stats(x["excess"] for x in rows if x["excess"] is not None),
        })
    return out


def hold_through_rows(conn, eligible_cases):
    rows = []
    for case in eligible_cases:
        stock, _ = attached_price_rows(conn, case)
        r = event_return(stock, case["event_at"][:10], case["announcement_session"])
        labels = db.outcome_label_rows(conn, case["case_id"])
        outcome = json.loads(labels[-1]["payload"]) if labels else {}
        rows.append({
            "id": case["case_id"], "ticker": case["ticker"], "date": case["event_at"][:10],
            "session": case["announcement_session"], "timing_uncertain": r.get("timing_uncertain", False),
            "move": r.get("return") if r.get("available") else None,
            "clinical": outcome.get("clinical"), "regulatory": outcome.get("regulatory"),
            "available": r.get("available", False),
        })
    return rows


def report(conn):
    cases = db.historical_case_rows(conn)
    clean = [case for case in cases if not case["legacy_post_hoc"]]
    readiness = {case["case_id"]: readiness_for_case(conn, case["case_id"]) for case in clean}
    runup_cases = [case for case in clean if readiness[case["case_id"]]["runup_ready"]]
    hold_cases = [case for case in clean if readiness[case["case_id"]]["hold_ready"]]
    event_rows = []
    for case in runup_cases:
        stock, xbi = attached_price_rows(conn, case)
        event_day = case["event_at"][:10]
        stock = [row for row in stock if row["date"] < event_day]
        xbi = [row for row in xbi if row["date"] < event_day]
        if stock:
            event_rows.append({"id": case["case_id"], "stock": stock, "benchmark": xbi})
    grids = all_runup_grids(event_rows)
    hold = hold_through_rows(conn, hold_cases)
    moves = [r["move"] for r in hold if r["move"] is not None]
    return {
        "coverage": {"historical_cases_total": len(cases),
                     "legacy_quarantined": len(cases) - len(clean),
                     "clean_cases_total": len(clean),
                     "research_ready_cases": sum(r["research_ready"] for r in readiness.values()),
                     "runup_ready_cases": len(runup_cases), "hold_ready_cases": len(hold_cases),
                     "runup_case_ids": [case["case_id"] for case in runup_cases],
                     "hold_case_ids": [case["case_id"] for case in hold_cases],
                     "runup_with_attached_prices": len(event_rows),
                     "hold_with_session_aware_move": len(moves)},
        "runup_all_grids": grids,
        "hold_through": {"stats": stats(moves), "rows": hold},
        "warnings": [
            "No grid is a validated edge merely because it looks best in-sample.",
            "Only non-legacy, case-attached runup-ready and hold-ready cases enter empirical statistics.",
            "Unknown announcement sessions and legacy post-hoc events are excluded from empirical statistics.",
            "Transaction costs, borrow constraints and gap slippage must be modeled before capital deployment.",
        ],
    }
