"""Walk-forward reporting for validation gates.

This evaluator is deliberately evidence-only: it updates observed sample counts and
metrics, but preserves each gate's existing ``enabled`` value exactly.
"""
from __future__ import annotations

from statistics import mean, median

from . import db
from .backtest_v2 import hold_through_rows, runup_one
from .historical import attached_price_rows, market_event_date, readiness_for_case
from .market import pre_event_prices, abnormal_return

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
                    "last_event_at": group[-1]["event_at"], "metrics": _stats(values),
                    "prior_n": sum(row["event_at"] < group[0]["event_at"] for row in rows[:offset]),
                    "descriptive_only": True})
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
            result = runup_one(pre_event_prices(stock, event_day, case["announcement_session"]),
                               pre_event_prices(benchmark, event_day, case["announcement_session"]), *RUNUP_WINDOW)
            if result and result["excess"] is not None:
                runup_rows.append({"case_id": case["case_id"], "event_at": case["event_at"], "excess": result["excess"],
                                   "cohort": cohort(case["catalyst_type"])})
        if readiness["hold_ready"]:
            hold_cases.append(case)
    hold_rows = [{"case_id": row["id"], "event_at": next(case["event_at"] for case in hold_cases if case["case_id"] == row["id"]), "move": row["move"]}
                 for row in hold_through_rows(conn, hold_cases) if row["move"] is not None]
    for row in hold_rows:
        case = next(case for case in hold_cases if case["case_id"] == row["case_id"])
        row["cohort"] = cohort(case["catalyst_type"])
        stock, bench = attached_price_rows(conn, case)
        row["abnormal_return"] = abnormal_return(stock, bench, market_event_date(case["event_at"]).isoformat(), case["announcement_session"])
    runup_metrics = {"evaluation": "descriptive_chronological_cohorts", "window": {"entry_trading_days": RUNUP_WINDOW[0], "exit_trading_days": RUNUP_WINDOW[1]}}
    hold_metrics = {"evaluation": "descriptive_chronological_cohorts"}
    # Reconstructed snapshots are useful descriptions, not prospective OOS evidence.
    # No calibrated/frozen strategy exists yet, so never relabel the convenience sample OOS.
    for metrics, rows, value in ((runup_metrics, runup_rows, "excess"), (hold_metrics, hold_rows, "move")):
        metrics.update(oos_n=0, descriptive_n=len(rows), eligible=False,
                       limitation="prospectively_registered_strategy_required")
        metrics["cohorts"] = {name: {"stats": _stats([r[value] for r in rows if r["cohort"] == name]),
                                    "folds": _folds([r for r in rows if r["cohort"] == name], value, folds),
                                    "rows": [r for r in rows if r["cohort"] == name]}
                              for name in ("P2_TOPLINE", "P3_TOPLINE", "PDUFA", "ADCOM", "OTHER")}
    for name, metrics in (("runup", runup_metrics), ("hold_through", hold_metrics)):
        existing = db.validation_row(conn, name)
        if not existing:
            raise RuntimeError(f"validation gate {name} is not initialized")
        db.set_validation(conn, name, bool(existing["enabled"]), metrics["oos_n"], existing["min_oos_n"], metrics,
                          "descriptive cohort validation only; enabled state preserved")
    return {"runup": runup_metrics, "hold_through": hold_metrics, "gates_auto_enabled": False}


def cohort(kind):
    return "PDUFA" if kind.startswith("PDUFA") else kind if kind in {"P2_TOPLINE", "P3_TOPLINE", "ADCOM"} else "OTHER"
