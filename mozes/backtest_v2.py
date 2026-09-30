"""Point-in-time backtesting v0.2.

Reports every run-up grid, uses calendar-aligned benchmarks, and uses announcement session
for hold-through returns. No strategy gate is unlocked from in-sample results.
"""
from __future__ import annotations

from statistics import mean, median, stdev

from . import db
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


def hold_through_rows(conn, historical_events):
    outcomes = db.load_outcomes(conn)
    rows = []
    for e in historical_events:
        state = db.event_state(conn, e["id"]) or {}
        stock = db.load_prices(conn, e.get("ticker"))
        r = event_return(stock, e.get("date"), state.get("event_session", "unknown"))
        o = outcomes.get(e["id"], {})
        rows.append({
            "id": e["id"], "ticker": e.get("ticker"), "date": e.get("date"),
            "session": state.get("event_session", "unknown"), "timing_uncertain": r.get("timing_uncertain", True),
            "move": r.get("return") if r.get("available") else None,
            "clinical": o.get("clinical"), "available": r.get("available", False),
        })
    return rows


def report(conn):
    hist = db.load_events(conn, "historical")
    event_rows = []
    for e in hist:
        stock = db.load_prices(conn, e.get("ticker"), before=e.get("date"))
        xbi = db.load_prices(conn, "XBI", before=e.get("date"))
        if stock:
            event_rows.append({"id": e["id"], "stock": stock, "benchmark": xbi})
    grids = all_runup_grids(event_rows)
    hold = hold_through_rows(conn, hist)
    moves = [r["move"] for r in hold if r["move"] is not None]
    return {
        "coverage": {"historical_events": len(hist), "with_pre_event_prices": len(event_rows), "with_session_aware_event_move": len(moves)},
        "runup_all_grids": grids,
        "hold_through": {"stats": stats(moves), "rows": hold},
        "warnings": [
            "No grid is a validated edge merely because it looks best in-sample.",
            "Unknown announcement sessions use next-close and are flagged timing_uncertain.",
            "Historical evidence annotations require blinded reconstruction before calibration.",
            "Transaction costs, borrow constraints and gap slippage must be modeled before capital deployment.",
        ],
    }
