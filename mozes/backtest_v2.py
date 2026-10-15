"""Point-in-time backtesting v0.2.

Reports every run-up grid, uses calendar-aligned benchmarks, and uses announcement session
for hold-through returns. No strategy gate is unlocked from in-sample results.
"""
from __future__ import annotations

import json
import math
from statistics import mean, median, stdev

from . import db
from .historical import attached_price_rows, market_event_date, readiness_for_case
from .market import event_return, join_on_dates, pre_event_prices
from .price_validation import validated_prices
from .backtest_research import DEFAULT_COSTS, cost_sensitivity, entry_audit

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
        "small_sample": len(xs) < 30,
        "uncertainty": "convenience sample; overlapping events are not independent; no validated edge",
    }


def _window_return(rows, entry, exit_):
    if (not isinstance(entry, int) or not isinstance(exit_, int) or
            isinstance(entry, bool) or isinstance(exit_, bool) or not 0 < exit_ < entry):
        raise ValueError('entry and exit must be positive ordered session offsets')
    if len(rows) < entry:
        return None
    # T-1 is last row. T-k uses k trading sessions before event reference.
    a = rows[-entry]
    b = rows[-exit_]
    return a, b, b["close"] / a["close"] - 1


def assumed_net_stats(returns, scenarios=DEFAULT_COSTS):
    values = list(returns)
    return [{'assumptions': {k: v for k, v in cost_sensitivity(0, [scenario])[0].items()
                             if k not in {'gross_return', 'net_return'}},
             'stats': stats(cost_sensitivity(value, [scenario])[0]['net_return'] for value in values)}
            for scenario in scenarios]


def runup_one(stock, bench, entry, exit_):
    try:
        stock = sorted(validated_prices(stock), key=lambda row: row['date'])
        if len({row['date'] for row in stock}) != len(stock):
            return None
    except ValueError:
        return None
    sr = _window_return(stock, entry, exit_)
    if not sr:
        return None
    a, b, r = sr
    if not math.isfinite(r) or r <= -1:
        return None
    try:
        bench = validated_prices(bench)
        if len({row['date'] for row in bench}) != len(bench):
            bench = []
    except ValueError:
        bench = []
    pairs = {s["date"]: (s, x) for s, x in join_on_dates(stock, bench)}
    ex = None
    if a["date"] in pairs and b["date"] in pairs:
        sa, ba = pairs[a["date"]]
        sb, bb = pairs[b["date"]]
        ex = r - (bb["close"] / ba["close"] - 1)
        if not math.isfinite(ex):
            ex = None
    return {"from": a["date"], "to": b["date"], "return": r, "excess": ex,
            "performance_kind": "descriptive_event_study", "executable": False,
            "session_basis": "observed_bars_calendar_unverified"}


def all_runup_grids(event_rows, cost_scenarios=DEFAULT_COSTS):
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
            "gross_return": stats(x["return"] for x in rows),
            "excess": stats(x["excess"] for x in rows if x["excess"] is not None),
            "performance_kind": "descriptive_event_study", "validated": False,
            "cases_considered": len(event_rows), "excluded_price_windows": len(event_rows) - len(rows),
            "missing_benchmark_windows": sum(x['excess'] is None for x in rows),
            "net_assumed": assumed_net_stats((row['return'] for row in rows), cost_scenarios),
        })
    return out


def hold_through_rows(conn, eligible_cases):
    rows = []
    for case in eligible_cases:
        stock, _ = attached_price_rows(conn, case)
        event_day = market_event_date(case["event_at"]).isoformat()
        r = event_return(stock, event_day, case["announcement_session"])
        labels = db.outcome_label_rows(conn, case["case_id"])
        outcome = json.loads(labels[-1]["payload"]) if labels else {}
        rows.append({
            "id": case["case_id"], "ticker": case["ticker"], "date": event_day,
            "session": case["announcement_session"], "timing_uncertain": r.get("timing_uncertain", False),
            "move": r.get("return") if r.get("available") else None,
            "clinical": outcome.get("clinical"), "regulatory": outcome.get("regulatory"),
            "available": r.get("available", False),
        })
    return rows


def report(conn, *, cost_scenarios=DEFAULT_COSTS):
    cases = db.historical_case_rows(conn)
    clean = [case for case in cases if not case["legacy_post_hoc"]]
    readiness = {case["case_id"]: readiness_for_case(conn, case["case_id"]) for case in clean}
    runup_cases = [case for case in clean if readiness[case["case_id"]]["runup_ready"]]
    hold_cases = [case for case in clean if readiness[case["case_id"]]["hold_ready"]]
    event_rows = []
    for case in runup_cases:
        stock, xbi = attached_price_rows(conn, case)
        event_day = market_event_date(case["event_at"]).isoformat()
        stock = pre_event_prices(stock, event_day, case["announcement_session"])
        xbi = pre_event_prices(xbi, event_day, case["announcement_session"])
        if stock:
            event_rows.append({"id": case["case_id"], "stock": stock, "benchmark": xbi})
    grids = all_runup_grids(event_rows, cost_scenarios)
    hold = hold_through_rows(conn, hold_cases)
    moves = [r["move"] for r in hold if r["move"] is not None]
    evidence = []
    reference = {row['id']: runup_one(row['stock'], row['benchmark'], 30, 7) for row in event_rows}
    for case in clean:
        window = reference.get(case['case_id'])
        audit = entry_audit(conn, case, window['from'] if window else None)
        audit['research_exclusions'] = readiness[case['case_id']]['reasons']
        evidence.append(audit)
    return {
        "performance_kind": "descriptive_event_study",
        "executable_strategy": {"supported": False, "validated": False, "n": 0, "oos_n": 0,
                                "reference_window": [30, 7], "excluded_cases": evidence},
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
        "hold_through": {"stats": stats(moves), "rows": hold,
                         "gross_stats": stats(moves),
                         "performance_kind": "descriptive_event_study",
                         "net_assumed": assumed_net_stats(moves, cost_scenarios)},
        "warnings": [
            "No grid is a validated edge merely because it looks best in-sample.",
            "All returns are descriptive final-event-date studies, not executable strategy returns or prospective OOS evidence.",
            "Observed bars are not a verified exchange calendar; missing sessions/holidays cannot establish executable T-k dates.",
            "Net returns use configurable illustrative commission/spread/slippage assumptions, not measured fills. Gaps, liquidity, impact and borrow remain unmodeled.",
            "META-M8 is unresolved: frozen attachments do not certify provider/adjustment/vintage provenance.",
            "Only non-legacy, case-attached runup-ready and hold-ready cases enter empirical statistics.",
            "Unknown announcement sessions and legacy post-hoc events are excluded from empirical statistics.",
            "Transaction costs, borrow constraints and gap slippage must be modeled before capital deployment.",
        ],
    }
