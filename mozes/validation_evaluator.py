"""Walk-forward reporting for validation gates.

This evaluator is deliberately evidence-only: it updates observed sample counts and
metrics, but preserves each gate's existing ``enabled`` value exactly.
"""
from __future__ import annotations

from statistics import mean, median

from . import db
from .backtest_v2 import hold_through_rows, runup_one
from .historical import attached_price_rows, market_event_date, readiness_for_case

RUNUP_WINDOW = (30, 7)  # predeclared; evaluator never searches grids for a winner


def _stats(values):
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": mean(values), "median": median(values),
            "win_rate": sum(value > 0 for value in values) / len(values)}


def _folds(rows, value_key, folds):
    rows = sorted(rows, key=lambda row: (row["event_at"], row["case_id"]))
    if not rows:
        return []
    size = max(1, (len(rows) + folds - 1) // folds)
    out = []
    for offset in range(0, len(rows), size):
        group = rows[offset:offset + size]
        values = [row[value_key] for row in group if row.get(value_key) is not None]
        out.append({"fold": len(out) + 1, "n": len(values), "first_event_at": group[0]["event_at"],
                    "last_event_at": group[-1]["event_at"], "metrics": _stats(values)})
    return out


def evaluate_walk_forward(conn, *, folds=5):
    """Persist reporting only; no code path here may enable a trading gate."""
    if folds < 2:
        raise ValueError("folds must be at least 2")
    clean = [case for case in db.historical_case_rows(conn) if not case["legacy_post_hoc"]]
    runup_rows, hold_cases = [], []
    for case in clean:
        readiness = readiness_for_case(conn, case["case_id"])
        if readiness["runup_ready"]:
            stock, benchmark = attached_price_rows(conn, case)
            event_day = market_event_date(case["event_at"]).isoformat()
            result = runup_one([row for row in stock if row["date"] < event_day],
                               [row for row in benchmark if row["date"] < event_day], *RUNUP_WINDOW)
            if result and result["excess"] is not None:
                runup_rows.append({"case_id": case["case_id"], "event_at": case["event_at"], "excess": result["excess"]})
        if readiness["hold_ready"]:
            hold_cases.append(case)
    hold_rows = [{"case_id": row["id"], "event_at": next(case["event_at"] for case in hold_cases if case["case_id"] == row["id"]), "move": row["move"]}
                 for row in hold_through_rows(conn, hold_cases) if row["move"] is not None]
    runup_metrics = {"evaluation": "walk_forward_reporting_only", "window": {"entry_trading_days": RUNUP_WINDOW[0], "exit_trading_days": RUNUP_WINDOW[1]},
                     "oos_n": len(runup_rows), "folds": _folds(runup_rows, "excess", folds), "aggregate": _stats([row["excess"] for row in runup_rows])}
    hold_metrics = {"evaluation": "walk_forward_reporting_only", "oos_n": len(hold_rows),
                    "folds": _folds(hold_rows, "move", folds), "aggregate": _stats([row["move"] for row in hold_rows])}
    for name, count, metrics, note in (("runup", len(runup_rows), runup_metrics, "walk-forward metrics recorded; gate remains manually locked"),
                                       ("hold_through", len(hold_rows), hold_metrics, "walk-forward metrics recorded; gate remains manually locked")):
        existing = db.validation_row(conn, name)
        if not existing:
            raise RuntimeError(f"validation gate {name} is not initialized")
        db.set_validation(conn, name, bool(existing["enabled"]), count, existing["min_oos_n"], metrics, note)
    return {"runup": runup_metrics, "hold_through": hold_metrics, "gates_auto_enabled": False}
