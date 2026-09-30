"""MOZES v0.2 scoring engine.

Key changes from v0.1:
- readout and regulatory catalysts use separate evidence engines;
- source verification/lifecycle are first-class;
- HOLD and RUN-UP are empirically gated, not enabled by heuristic thresholds;
- live prices are read from SQLite rather than legacy JSON payloads;
- stale/unverified events cannot become actionable signals.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from . import db
from .data_loader import load_sources
from .dates import date_info, days_between
from .lifecycle import is_resolved
from .market import join_on_dates
from .scoring import catalyst_impact, risk_flags

READOUT_TYPES = {"P3_TOPLINE", "P2_TOPLINE", "P12_DATA", "INTERIM"}
REG_TYPES = {"PDUFA_NME", "PDUFA_SUPP", "PDUFA_GENERIC", "ADCOM"}
INFO_TYPES = {"FILING", "CONFERENCE"}

# BIO/Informa/QLS 2011-2020 program transition rates. These are context priors,
# not literal probabilities that a particular topline is positive.
BIO_PRIORS = {
    "P3_TOPLINE": 0.578,
    "P2_TOPLINE": 0.289,
    "P12_DATA": 0.289,
    "PDUFA_NME": 0.906,
    "PDUFA_SUPP": 0.906,
    "PDUFA_GENERIC": 0.906,
}


def _clamp(x, lo=5, hi=95):
    return max(lo, min(hi, int(round(x))))


def _cat(score):
    if score >= 80:
        return "Very Strong"
    if score >= 65:
        return "Strong"
    if score >= 45:
        return "Moderate"
    return "Weak"


def readout_evidence(event: dict) -> dict:
    f = event.get("features") or {}
    prior = BIO_PRIORS.get(event.get("type"), 0.50)
    if event.get("ta") == "oncology" and event.get("type") == "P3_TOPLINE":
        prior = 0.477
    score = prior * 100
    reasons = [{"code": "base_rate", "delta": 0, "text": f"historical program-transition context {prior:.1%}; not a topline probability"}]

    adj = {
        "strong": 14,
        "moderate": 5,
        "weak": -10,
        "none": -15,
    }
    if f.get("prior") in adj:
        d = adj[f["prior"]]
        score += d
        reasons.append({"code": "prior_efficacy", "delta": d, "text": f"prior efficacy: {f['prior']}"})
    if f.get("endpoint_consistent") is True:
        score += 8; reasons.append({"code": "endpoint", "delta": 8, "text": "primary endpoint continuity"})
    elif f.get("endpoint_consistent") == "partial":
        score += 2; reasons.append({"code": "endpoint", "delta": 2, "text": "partial endpoint continuity"})
    elif f.get("endpoint_consistent") is False:
        score -= 14; reasons.append({"code": "endpoint", "delta": -14, "text": "endpoint changed from prior evidence"})
    if f.get("population_consistent") is True:
        score += 5; reasons.append({"code": "population", "delta": 5, "text": "population continuity"})
    elif f.get("population_consistent") is False:
        score -= 14; reasons.append({"code": "population", "delta": -14, "text": "population changed materially"})
    if f.get("regimen_changed"):
        score -= 7; reasons.append({"code": "regimen", "delta": -7, "text": "dose/regimen changed"})
    design = f.get("design")
    if design in {"rct_placebo", "rct_sham"}:
        score += 5; reasons.append({"code": "design", "delta": 5, "text": "randomized controlled design"})
    elif design == "rct_active_ni":
        score += 1; reasons.append({"code": "design", "delta": 1, "text": "active-control non-inferiority design"})
    elif design in {"single_arm", "external_control"}:
        score -= 9; reasons.append({"code": "design", "delta": -9, "text": "uncontrolled/external-control design"})
    if f.get("objective") is True:
        score += 4; reasons.append({"code": "objective", "delta": 4, "text": "objective endpoint"})
    elif f.get("objective") is False:
        score -= 4; reasons.append({"code": "objective", "delta": -4, "text": "subjective/assessment-dependent endpoint"})
    if f.get("mechanism_validated") is True:
        score += 5; reasons.append({"code": "mechanism", "delta": 5, "text": "clinically validated mechanism/class"})
    elif f.get("mechanism_validated") is False:
        score -= 3; reasons.append({"code": "mechanism", "delta": -3, "text": "novel/unvalidated mechanism"})
    if f.get("class_failures"):
        score -= 6; reasons.append({"code": "class_failures", "delta": -6, "text": "relevant class failures"})
    if f.get("safety_concern"):
        score -= 10; reasons.append({"code": "safety", "delta": -10, "text": "known safety concern"})
    if f.get("integrity"):
        score -= 35; reasons.append({"code": "integrity", "delta": -35, "text": "data-integrity concern"})
    if f.get("n") is not None and f["n"] < 100:
        score -= 5; reasons.append({"code": "sample", "delta": -5, "text": "small sample"})
    s = _clamp(score)
    return {"engine": "clinical_readout_v0.2", "score": s, "category": _cat(s), "context_prior": prior, "reasons": reasons,
            "probability_label": "UNCALIBRATED; do not interpret score as probability"}


def regulatory_evidence(event: dict) -> dict:
    f = event.get("features") or {}
    base = BIO_PRIORS.get(event.get("type"), 0.70 if event.get("type") == "ADCOM" else 0.906)
    score = base * 100
    reasons = [{"code": "base_rate", "delta": 0, "text": f"historical application-transition context {base:.1%}"}]
    if f.get("prior") == "strong":
        score += 4; reasons.append({"code": "clinical_package", "delta": 4, "text": "strong underlying efficacy package"})
    elif f.get("prior") == "weak":
        score -= 12; reasons.append({"code": "clinical_package", "delta": -12, "text": "weak underlying efficacy package"})
    if f.get("prior_crl"):
        score -= 20; reasons.append({"code": "prior_crl", "delta": -20, "text": "prior Complete Response Letter"})
    if f.get("negative_adcom"):
        score -= 35; reasons.append({"code": "negative_adcom", "delta": -35, "text": "negative advisory committee vote"})
    if f.get("fda_requirement_at_risk"):
        score -= 30; reasons.append({"code": "fda_requirement", "delta": -30, "text": "public FDA requirement appears at risk"})
    if f.get("safety_concern"):
        score -= 10; reasons.append({"code": "safety", "delta": -10, "text": "known safety issue"})
    if f.get("integrity"):
        score -= 30; reasons.append({"code": "integrity", "delta": -30, "text": "data-integrity concern"})
    s = _clamp(score)
    return {"engine": "regulatory_v0.2", "score": s, "category": _cat(s), "context_prior": base, "reasons": reasons,
            "probability_label": "UNCALIBRATED; do not interpret score as probability"}


def informational_evidence(event: dict) -> dict:
    return {"engine": "informational_v0.2", "score": None, "category": "Informational", "context_prior": None,
            "reasons": [{"code": "non_binary", "delta": 0, "text": "not treated as a primary binary decision event"}]}


def evidence_for(event: dict) -> dict:
    t = event.get("type")
    if t in READOUT_TYPES:
        return readout_evidence(event)
    if t in REG_TYPES:
        return regulatory_evidence(event)
    return informational_evidence(event)


def market_setup_from_db(conn, ticker: str, as_of: str, benchmark="XBI") -> dict:
    p = db.load_prices(conn, ticker, before=as_of)
    b = db.load_prices(conn, benchmark, before=as_of)
    if len(p) < 31:
        return {"available": False, "n_prices": len(p), "benchmark": benchmark, "reason": "fewer than 31 pre-event closes in DB"}
    last = p[-1]
    rets = {}
    for n in (7, 14, 30, 60, 90, 120):
        if len(p) > n:
            rets[f"T-{n}"] = last["close"] / p[-1 - n]["close"] - 1
    rel = {}
    pairs = join_on_dates(p, b)
    if len(pairs) >= 31:
        slast, blast = pairs[-1]
        for n in (7, 14, 30, 60, 90, 120):
            if len(pairs) > n:
                s0, b0 = pairs[-1 - n]
                rel[f"T-{n}"] = (slast["close"] / s0["close"] - 1) - (blast["close"] / b0["close"] - 1)
    lrs = [math.log(p[i]["close"] / p[i - 1]["close"]) for i in range(max(1, len(p)-20), len(p))]
    vol = None
    if len(lrs) > 1:
        m = sum(lrs) / len(lrs)
        vol = math.sqrt(sum((x-m)**2 for x in lrs)/(len(lrs)-1))*math.sqrt(252)
    return {"available": True, "n_prices": len(p), "last_close": last, "returns": rets, "relative_to_xbi": rel,
            "realized_vol_20d": vol, "benchmark": benchmark}


def classify_v2(event, state, impact, evidence, flags, market, date_conf, gates, today, window):
    status = state.get("status") or "CANDIDATE"
    if is_resolved(status):
        return {"class": "RESOLVED", "actionable": False, "reasons": [f"event status is {status}"]}
    if state.get("verification_state") == "QUARANTINED" or status == "QUARANTINED":
        return {"class": "QUARANTINED", "actionable": False, "reasons": ["primary-source verification missing or conflicting"]}
    stale = window and window.get("end") and window["end"] < today
    if stale:
        return {"class": "STALE_UNRESOLVED", "actionable": False, "reasons": ["expected window passed without a resolved lifecycle update"]}
    if any(f.get("sev") == "critical" for f in flags):
        return {"class": "AVOID_BINARY", "actionable": True, "reasons": ["critical risk flag"]}
    if evidence.get("score") is not None and evidence["score"] < 45:
        return {"class": "AVOID_BINARY", "actionable": True, "reasons": ["weak evidence/regulatory setup"]}
    if event.get("type") in INFO_TYPES:
        return {"class": "WATCH", "actionable": True, "reasons": ["informational catalyst, not modeled as primary binary"]}
    if gates["hold_through"]["satisfied"] and evidence.get("score", 0) >= 75 and market.get("available") and date_conf >= 80:
        return {"class": "HOLD_THROUGH_CANDIDATE", "actionable": True, "reasons": ["empirical hold gate satisfied"]}
    if gates["runup"]["satisfied"] and impact["score"] >= 70 and date_conf >= 55:
        return {"class": "RUNUP_CANDIDATE", "actionable": True, "reasons": ["empirical run-up gate satisfied"]}
    return {"class": "REVIEW", "actionable": True, "reasons": ["research-worthy; trading gates remain locked"]}


def recommendation_status(state, impact, evidence, flags, market, classification, gates, security=None) -> dict:
    """Human-readable research recommendation; never a calibrated probability or gate override."""
    blocked = {"RESOLVED", "QUARANTINED", "STALE_UNRESOLVED"}
    cls = classification["class"]
    verified = state.get("verification_state") == "VERIFIED"
    severe = any(f.get("sev") in {"high", "critical"} for f in flags)
    base = {"experimental": True, "validation_gates_separate": True}
    if security is not None and not security.get("tradable"):
        why = "הטיקר אינו נסחר עוד." if (security or {}).get("status") in {"ACQUIRED","DELISTED","SUSPENDED","BANKRUPT"} else "לא נמצא אימות עדכני שהטיקר נסחר בציבור."
        return {**base, "status": "INSUFFICIENT_INFORMATION", "label_he": "חסר מידע", "why_he": why, "missing_he": "אימות מצב מסחר ממקור רשמי."}
    if cls in blocked or not verified:
        return {**base, "status": "INSUFFICIENT_INFORMATION", "label_he": "חסר מידע", "why_he": "האירוע אינו מאומת מספיק מול מקור ראשוני.", "missing_he": "אימות מועד ואירוע מול מקור ראשוני."}
    if cls == "AVOID_BINARY" or severe:
        return {**base, "status": "NOT_NOW", "label_he": "לא כרגע", "why_he": "דגל סיכון מהותי או ראיות חלשות מגבילים את ההתקדמות.", "missing_he": "שינוי מהותי בפרופיל הסיכון או ראיות חדשות."}
    if evidence.get("score") is not None and impact["score"] >= 70 and evidence["score"] >= 75 and market.get("available"):
        return {**base, "status": "INVESTMENT_CANDIDATE", "label_he": "מועמדת להשקעה", "why_he": "אירוע משמעותי, ראיות חזקות, אימות ראשוני ותמונת שוק זמינה.", "missing_he": "אימות אמפירי מתקדם עדיין נדרש לפני החלטת run-up או החזקה דרך אירוע."}
    if evidence.get("score") is not None and impact["score"] >= 65 and evidence["score"] >= 65:
        return {**base, "status": "APPROACHING_CANDIDATE", "label_he": "מתקרבת למועמדות", "why_he": "השילוב של חשיבות, ראיות ואימות נראה מבטיח.", "missing_he": "תמונת שוק מלאה או חיזוק נוסף של הראיות."}
    if cls == "WATCH":
        return {**base, "status": "WATCH", "label_he": "מעקב", "why_he": "האירוע אינו אירוע בינארי מרכזי במודל.", "missing_he": "טריגר מהותי יותר או נתונים חדשים."}
    return {**base, "status": "RESEARCH_WORTHY", "label_he": "שווה מחקר", "why_he": "האירוע מאומת וראוי לבדיקת עומק, אך אינו עומד בכל תנאי המועמדות.", "missing_he": "חיזוק של חשיבות האירוע, ראיות או תמונת שוק."}


def score_event(conn, event: dict, today: str) -> dict:
    sources = load_sources()
    di = date_info(event.get("chronology", []), sources)
    state = db.event_state(conn, event["id"]) or {}
    security = __import__('mozes.security', fromlist=['tradability']).tradability(conn, event.get("ticker"))
    impact = catalyst_impact(event)
    evidence = evidence_for(event)
    flags = risk_flags(event)
    market = market_setup_from_db(conn, event.get("ticker"), (date.fromisoformat(today) + timedelta(days=1)).isoformat()) if event.get("ticker") else {"available": False}
    gates = __import__('mozes.radar', fromlist=['validation_status']).validation_status(conn)
    classification = classify_v2(event, state, impact, evidence, flags, market, state.get("verification_confidence", di.get("confidence", 0)), gates, today, di.get("window"))
    recommendation = recommendation_status(state, impact, evidence, flags, market, classification, gates, security)
    days_to = None
    if di.get("window") and di["window"].get("start"):
        days_to = days_between(today, di["window"]["start"])
    return {
        "id": event["id"], "ticker": event.get("ticker"), "company": event.get("company"),
        "program": event.get("program"), "indication": event.get("indication"), "ta": event.get("ta"),
        "type": event.get("type"), "phase": event.get("phase"), "dependency": event.get("dependency"),
        "commercial": bool(event.get("commercial")), "pivotal": bool(event.get("pivotal")),
        "today": today, "as_of": today, "state": state, "date": di, "days_to": days_to,
        "impact": impact, "evidence": evidence, "market": market, "risk_flags": flags,
        "classification": classification, "recommendation": recommendation, "gates": gates, "sources": db.load_event_sources(conn, event["id"]),
        "features": event.get("features") or {}, "security": security,
    }
