"""Backtesting. Run-up returns come from point-in-time snapshots (prices strictly before T).
Parameter selection is walk-forward: chosen on prior years only, evaluated on the next year."""
from __future__ import annotations

import math
from statistics import mean, stdev

from .dates import days_between
from .pit import snapshot_at
from .scenarios import REG_TYPES, quantile
from .versions import RUNUP_EDGE_VALIDATED

GRID = [(e, x) for e in (60, 45, 30, 21, 14, 7) for x in (14, 7, 3, 1) if x < e]


def stats(xs):
    xs = list(xs)
    n = len(xs)
    if not n:
        return {"n": 0}
    s = sorted(xs)
    m = mean(xs)
    sd = stdev(xs) if n > 1 else 0.0
    return {"n": n, "mean": m, "median": quantile(s, 0.5), "win_rate": sum(1 for x in xs if x > 0) / n,
            "sd": sd, "worst": s[0], "best": s[-1], "sharpe_like": (m / sd) if sd else None}


def max_drawdown(rets):
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    return dd


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def run_up_return(snap, entry, exit_):
    """Return from T-entry close to T-exit close (trading-day offsets, T-1 = last close before T)."""
    p, b = snap["prices"], snap["bench"]
    n = len(p)
    if n < entry or days_between(p[-1]["date"], snap["as_of"]) > 5:
        return None
    r = p[n - exit_]["close"] / p[n - entry]["close"] - 1
    ex = None
    if len(b) >= entry:
        ex = r - (b[len(b) - exit_]["close"] / b[len(b) - entry]["close"] - 1)
    return {"ret": r, "excess": ex}


def run_up_backtest(events, entry, exit_):
    rows = []
    for e in sorted(events, key=lambda e: e["date"]):
        r = run_up_return(snapshot_at(e, e["date"]), entry, exit_)
        if r:
            rows.append({"id": e["id"], "date": e["date"], **r})
    ex = [r["excess"] for r in rows if r["excess"] is not None]
    return {"entry": entry, "exit": exit_, "n": len(rows), "ret": stats(r["ret"] for r in rows),
            "excess": stats(ex), "max_drawdown": max_drawdown([r["ret"] for r in rows]), "rows": rows}


def walk_forward(events, grid=GRID, min_train=5):
    years = sorted({e["date"][:4] for e in events})
    folds = []
    for y in years:
        train = [e for e in events if e["date"][:4] < y]
        test = [e for e in events if e["date"][:4] == y]
        best = None
        for entry, exit_ in grid:
            bt = run_up_backtest(train, entry, exit_)
            if bt["n"] < min_train:
                continue
            key = bt["excess"] if bt["excess"]["n"] else bt["ret"]
            if best is None or key["median"] > best[1]:
                best = ((entry, exit_), key["median"])
        if best is None:
            folds.append({"year": y, "skipped": True, "reason": "insufficient training events with price windows"})
            continue
        te = run_up_backtest(test, *best[0])
        folds.append({"year": y, "skipped": False, "chosen": best[0], "train_median": best[1],
                      "test": {k: te[k] for k in ("n", "ret", "excess", "max_drawdown")}})
    return folds


def bucket_evidence(s):
    return "80-100" if s >= 80 else "60-79" if s >= 60 else "40-59" if s >= 40 else "0-39"


def bucket_impact(s):
    return "70-100" if s >= 70 else "50-69" if s >= 50 else "0-49"


def full_report(hist_analyses, historical, outcomes):
    by_id = {e["id"]: e for e in historical}
    rows = []
    for a in hist_analyses:
        e, o = by_id[a["id"]], outcomes.get(a["id"], {})
        rows.append({"id": a["id"], "ticker": e["ticker"], "date": e["date"], "type": e["type"],
                     "phase": e.get("phase"), "mcap": e.get("mcap", "unknown"), "program": e.get("program"),
                     "evidence": a["evidence"]["score"], "impact": a["impact"]["score"],
                     "cls": a["classification"]["cls"], "clinical": o.get("clinical"),
                     "move": o.get("move"), "verified": o.get("verified", False)})
    groups = {
        "type_group": lambda r: "regulatory" if r["type"] in REG_TYPES else "readout",
        "phase": lambda r: r["phase"], "mcap": lambda r: r["mcap"],
        "evidence_bucket": lambda r: bucket_evidence(r["evidence"]),
        "impact_bucket": lambda r: bucket_impact(r["impact"]),
        "clinical_outcome": lambda r: r["clinical"],
    }
    hold = {}
    for g, fn in groups.items():
        d = {}
        for r in rows:
            d.setdefault(str(fn(r)), []).append(r)
        hold[g] = []
        for k, v in sorted(d.items()):
            moves = [r["move"] for r in v if r["move"] is not None]
            st = {kk: vv for kk, vv in stats(moves).items() if kk != "n"}
            hold[g].append({"key": k, "n": len(v), "n_move": len(moves), **st})
    calib = []
    for b in ("80-100", "60-79", "40-59", "0-39"):
        v = [r for r in rows if bucket_evidence(r["evidence"]) == b and r["clinical"] in ("success", "fail")]
        k = sum(1 for r in v if r["clinical"] == "success")
        lo, hi = wilson(k, len(v))
        calib.append({"bucket": b, "n": len(v), "successes": k, "rate": (k / len(v)) if v else None, "ci95": [lo, hi]})
    cvo = {}
    for r in rows:
        d = cvo.setdefault(r["cls"], {})
        d[str(r["clinical"])] = d.get(str(r["clinical"]), 0) + 1
    eligible = [e for e in historical if run_up_return(snapshot_at(e, e["date"]), 30, 1)]
    by_outcome = {}
    for r in rows:
        by_outcome[str(r["clinical"])] = by_outcome.get(str(r["clinical"]), 0) + 1
    return {
        "coverage": {"n_events": len(rows), "date_min": min(r["date"] for r in rows) if rows else None,
                     "date_max": max(r["date"] for r in rows) if rows else None, "by_outcome": by_outcome,
                     "n_with_move": sum(1 for r in rows if r["move"] is not None),
                     "n_verified_move": sum(1 for r in rows if r["verified"] is True),
                     "n_with_price_window": len(eligible)},
        "hold_through": hold, "calibration": calib, "class_vs_outcome": cvo,
        "run_up": {"validated": RUNUP_EDGE_VALIDATED, "n_with_price_window": len(eligible),
                   "status": "computed" if eligible else "not computable: no verified pre-event price windows ingested",
                   "walk_forward": walk_forward(historical)},
        "events": sorted(rows, key=lambda r: r["date"]),
        "caveats": [
            "n is tiny; results are illustrative, not evidence of an edge",
            "features were annotated post hoc by an annotator who knew outcomes: contaminated, needs blinded re-annotation",
            "moves are approximate recollections unless verified=true; replace with ingested closes",
            "the seed set over-represents famous large moves (selection bias)",
        ],
    }
