"""Legacy v0.1 point-in-time pipeline, retained only for frozen regression tests.

Supported product, API, and CLI scoring paths use ``engine_v2.score_event``.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from .data_loader import load_historical, load_live, load_outcomes, load_sources
from .dates import date_info, days_between
from .pit import snapshot_at, thaw
from .scenarios import reference_class, scenario_for
from .scoring import _legacy_catalyst_impact as catalyst_impact, classify, clinical_evidence
from .scoring import _legacy_risk_flags as risk_flags
from .versions import VERSIONS


def market_setup(snap):
    p, b = snap["prices"], snap["bench"]
    if len(p) < 31:
        return {"available": False, "n_prices": len(p),
                "note": "נדרשים לפחות 31 ימי מסחר לפני האירוע; מקור מחירים לא מחובר/לא נקלט",
                "version": VERSIONS["market_setup"]}
    offs = [o for o in (120, 90, 60, 30, 14, 7, 3) if len(p) >= o]
    last = p[-1]["close"]
    rets = {f"T-{o}": last / p[len(p) - o]["close"] - 1 for o in offs}
    rel = {}
    if len(b) >= 31:
        bl = b[-1]["close"]
        rel = {f"T-{o}": rets[f"T-{o}"] - (bl / b[len(b) - o]["close"] - 1) for o in offs if len(b) >= o}
    lr = [math.log(p[i]["close"] / p[i - 1]["close"]) for i in range(len(p) - 20, len(p))]
    m = sum(lr) / len(lr)
    vol = math.sqrt(sum((x - m) ** 2 for x in lr) / (len(lr) - 1)) * math.sqrt(252)
    return {"available": True, "returns_to_t_minus_1": rets, "relative_to_benchmark": rel,
            "realized_vol_20d_annualized": vol, "version": VERSIONS["market_setup"]}


def analyze(event, as_of, historical, outcomes, sources, today):
    snap = snapshot_at(event, as_of)
    impact = catalyst_impact(snap)
    evidence = clinical_evidence(snap)
    flags = risk_flags(snap)
    di = date_info(snap["chronology"], sources)
    market = market_setup(snap)
    rc = reference_class(snap["type"], as_of, historical, outcomes)
    scen = scenario_for(snap, evidence, rc)
    days_to = days_between(today, di["window"]["start"]) if di["window"] and di["window"]["start"] else None
    cls = classify(impact, evidence, flags, scen, di["confidence"], market["available"], days_to)
    return {"id": event["id"], "ticker": event.get("ticker"), "as_of": as_of, "snapshot": thaw(snap),
            "impact": impact, "evidence": evidence, "flags": flags, "date": di, "market": market,
            "scenario": scen, "classification": cls, "versions": dict(VERSIONS)}


def run_all(today: date | None = None):
    today = today or date.today()
    t = today.isoformat()
    live_as_of = (today + timedelta(days=1)).isoformat()  # everything known by end of `today`
    sources, historical, outcomes, live = load_sources(), load_historical(), load_outcomes(), load_live()
    return {
        "today": t,
        "live": [analyze(e, live_as_of, historical, outcomes, sources, t) for e in live],
        "historical": [analyze(e, e["date"], historical, outcomes, sources, t) for e in historical],
        "historical_events": historical, "outcomes": outcomes, "sources": sources,
    }
