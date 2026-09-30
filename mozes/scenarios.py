"""Reference-class scenarios + expected-value ranges.

The reference class uses ONLY events whose catalyst date is strictly before as_of.
These ranges are crude: the seed dataset is biased toward memorable large moves,
and they ignore what the market price already implies. Treat as context, not valuation.
"""
from __future__ import annotations

import math

REG_TYPES = {"PDUFA_NME", "PDUFA_SUPP", "ADCOM", "FILING"}
EXPOSURE = {"single": 1.0, "lead": 0.8, "one_of_few": 0.5, "one_of_many": 0.15}
MCAP_DAMP = {"mega": 0.3, "large": 0.8}


def quantile(sorted_xs, q):
    if not sorted_xs:
        return None
    i = (len(sorted_xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (i - lo)


def reference_class(event_type, as_of, historical, outcomes):
    reg = event_type in REG_TYPES
    ids, up, dn = [], [], []
    for h in historical:
        if h["date"] >= as_of or (h["type"] in REG_TYPES) != reg:
            continue
        o = outcomes.get(h["id"])
        if not o or o.get("move") is None:
            continue
        ids.append(h["id"])
        (up if o["clinical"] == "success" else dn).append(o["move"])
    up.sort()
    dn.sort()
    return {
        "group": "regulatory" if reg else "readout", "ids": ids, "n_up": len(up), "n_down": len(dn),
        "bull": [quantile(up, 0.25), quantile(up, 0.75)] if len(up) >= 3 else None,
        "bear": [quantile(dn, 0.25), quantile(dn, 0.75)] if len(dn) >= 2 else None,
    }


def ev_range(p_lo, p_hi, bull, bear):
    combos = [p * u + (1 - p) * d for p in (p_lo, p_hi) for u in bull for d in bear]
    pm, um, dm = (p_lo + p_hi) / 2, sum(bull) / 2, sum(bear) / 2
    return {"ev_min": min(combos), "ev_max": max(combos), "ev_mid": pm * um + (1 - pm) * dm,
            "asymmetry": (um / abs(dm)) if dm else None, "expected_abs_move": pm * abs(um) + (1 - pm) * abs(dm)}


def scenario_for(snap, evidence, rc):
    if not rc["bull"] or not rc["bear"]:
        return {"available": False, "reference": rc, "why": "אין מספיק אירועים שנפתרו לפני התאריך לבניית קבוצת ייחוס"}
    k = EXPOSURE.get(snap.get("dependency"), 0.8) * MCAP_DAMP.get(snap.get("mcap"), 1.0)
    bull = [x * k for x in rc["bull"]]
    bear = [x * k for x in rc["bear"]]
    p = evidence["model_p"]
    return {"available": True, "exposure_factor": k, "bull": bull, "bear": bear, "bear_mid": sum(bear) / 2,
            "p": p, **ev_range(p["lo"], p["hi"], bull, bear), "reference": rc,
            "caveat": "קבוצת ייחוס קטנה ומוטה לאירועים זכורים; אינה מתחשבת במה שהמחיר כבר מגלם"}


def implied_move(price, straddle=None, iv=None, days=None, open_interest=None, spread_pct=None):
    """Options-implied move. straddle/price, or ~0.8*IV*sqrt(days/365). Flags illiquid chains."""
    res = {"implied_move": None, "method": None, "reliable": True, "warnings": []}
    if straddle and price:
        res.update(implied_move=straddle / price, method="atm_straddle")
    elif iv and days:
        res.update(implied_move=0.8 * iv * math.sqrt(days / 365), method="iv_approx")
    if open_interest is not None and open_interest < 500:
        res["reliable"] = False
        res["warnings"].append("open interest < 500")
    if spread_pct is not None and spread_pct > 0.10:
        res["reliable"] = False
        res["warnings"].append("bid-ask spread > 10%")
    return res
