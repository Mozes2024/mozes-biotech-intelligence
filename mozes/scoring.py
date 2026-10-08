"""Legacy v0.1 scoring compatibility module; not used by product paths.

The active engine is :mod:`mozes.engine_v2`. This module remains importable only
for frozen regression fixtures; its evidence and classification are retired.
"""
from __future__ import annotations

import math

from .versions import VERSIONS

# Legacy-only fixture guard. Active gating is read from model_validation by engine_v2.
RUNUP_EDGE_VALIDATED = False


def rnd(x: float) -> int:
    return int(math.floor(x + 0.5))


TYPE_POINTS = {"P3_TOPLINE": 30, "P2_TOPLINE": 22, "P12_DATA": 20, "INTERIM": 20, "PDUFA_NME": 26, "PDUFA_GENERIC": 20,
               "ADCOM": 24, "PDUFA_SUPP": 12, "FILING": 8, "CONFERENCE": 10}
DEP_POINTS = {"single": 25, "lead": 18, "one_of_few": 12, "one_of_many": 5}
MCAP_POINTS = {"micro": 20, "small": 20, "mid": 16, "large": 10, "mega": 3, "unknown": 8}
TYPE_HE = {"P3_TOPLINE": "תוצאות Topline שלב 3", "P2_TOPLINE": "תוצאות Topline שלב 2", "P12_DATA": "נתוני שלב 1/2",
           "INTERIM": "ניתוח ביניים", "PDUFA_GENERIC": "PDUFA", "PDUFA_NME": "PDUFA — מוצר חדש", "ADCOM": "ועדה מייעצת FDA",
           "PDUFA_SUPP": "PDUFA — התוויה משלימה", "FILING": "הגשת NDA/BLA", "CONFERENCE": "הצגה בכנס"}
DEP_HE = {"single": "נכס יחיד", "lead": "נכס מוביל", "one_of_few": "אחד מכמה נכסים מתקדמים", "one_of_many": "אחד מרבים"}
MCAP_HE = {"micro": "<$300M", "small": "$300–500M", "mid": "$0.5–2B", "large": "$2–10B", "mega": ">$10B", "unknown": "לא ידוע"}

# Base rates: phase-transition rates, NOT "topline positive" rates. See docs/METHODOLOGY.md.
BASE_RATES = {
    "P3_TOPLINE": (0.581, "BIO/Biomedtracker 2006–2015: מעבר שלב 3→הגשה 58.1%"),
    "P2_TOPLINE": (0.307, "BIO/Biomedtracker 2006–2015: מעבר שלב 2→3 30.7%"),
    "P12_DATA": (0.307, "קירוב: שיעור שלב 2"),
    "INTERIM": (0.45, "placeholder — לא מבוסס מקור"),
    "PDUFA_NME": (0.853, "BIO/Biomedtracker 2006–2015: הגשה→אישור 85.3%"),
    "PDUFA_SUPP": (0.90, "placeholder — לא מבוסס מקור"),
    "ADCOM": (0.60, "placeholder — לא מבוסס מקור"),
    "FILING": (0.90, "placeholder — לא מבוסס מקור"),
    "CONFERENCE": (0.50, "placeholder — לא משמעותי לאירוע הצגה"),
}
# Approximate therapeutic-area multipliers (direction per BIO reports; magnitudes are assumptions).
TA_MULT = {"oncology": 0.8, "hematology": 1.15, "neurology": 0.9, "psychiatry": 0.9, "neuromuscular": 1.05}
PRIOR_PTS = {"strong": 12, "moderate": 5, "weak": -8, "none": -12}
PRIOR_HE = {"strong": "חזקות", "moderate": "בינוניות", "weak": "חלשות", "none": "אין"}
DESIGN_PTS = {"rct_placebo": 4, "rct_sham": 4, "rct_active_ni": 2, "single_arm": -6, "external_control": -6}
DESIGN_HE = {"rct_placebo": "אקראי מול פלצבו", "rct_sham": "אקראי מול sham", "rct_active_ni": "אקראי מול טיפול פעיל (אי-נחיתות)",
             "single_arm": "זרוע יחידה ללא ביקורת", "external_control": "ביקורת חיצונית/היסטורית"}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
EVIDENCE_CHECKS = ("prior", "endpoint_consistent", "population_consistent", "regimen_changed", "design",
                   "objective", "mechanism_validated", "safety_concern", "n")


def _legacy_catalyst_impact(s):
    c = []

    def add(code, p, why):
        c.append({"code": code, "points": p, "why": why})

    t = s.get("type")
    add("type", TYPE_POINTS.get(t, 10), f"סוג אירוע: {TYPE_HE.get(t, t)}")
    if s.get("pivotal") and t in ("P2_TOPLINE", "P12_DATA"):
        add("pivotal", 4, "המחקר מוגדר פיבוטלי/רישומי")
    dep = s.get("dependency")
    add("dependency", DEP_POINTS.get(dep, 8), f"תלות החברה בתוכנית: {DEP_HE.get(dep, 'לא ידוע')}")
    mc = s.get("mcap") or "unknown"
    add("mcap", MCAP_POINTS.get(mc, 8), f"שווי שוק לפני האירוע: {MCAP_HE.get(mc, mc)}")
    if s.get("commercial"):
        add("stage", 3, "חברה מסחרית — הכנסות קיימות מרככות את התגובה")
    else:
        add("stage", 10, "חברה טרום-מסחרית")
    if s.get("enables_filing"):
        add("filing", 10, "הצלחה מאפשרת הגשה/אישור ישירות")
    rw = s.get("runway_months")
    if rw is not None and rw < 12:
        add("runway", 5, "מסלול מזומנים קצר מ-12 חודשים — התוצאה תקבע את תנאי הגיוס")
    return {"score": min(100, sum(x["points"] for x in c)), "contributions": c, "version": VERSIONS["catalyst_impact"]}


def evidence_category(score):
    return "Very Strong" if score >= 80 else "Strong" if score >= 60 else "Moderate" if score >= 40 else "Weak"


def clinical_evidence(s):
    t = s.get("type")
    base_p, base_src = BASE_RATES.get(t, (0.5, "placeholder"))
    mult = TA_MULT.get(s.get("ta"), 1.0)
    base = min(0.95, base_p * mult)
    c = [{"code": "base_rate", "points": rnd(base * 100),
          "why": f"נקודת פתיחה — שיעור בסיס: {base_src}" + (f" × מקדם תחום טיפולי {mult}" if mult != 1.0 else "")}]
    f = s.get("features") or {}

    def add(code, p, why):
        c.append({"code": code, "points": p, "why": why})

    if f.get("prior") in PRIOR_PTS:
        add("prior", PRIOR_PTS[f["prior"]], f"ראיות יעילות קודמות: {PRIOR_HE[f['prior']]}")
    ep = f.get("endpoint_consistent")
    if ep is True:
        add("endpoint", 6, "נקודת הסיום זהה לשלב הקודם")
    elif ep == "partial":
        add("endpoint", 2, "נקודת הסיום דומה חלקית לשלב הקודם")
    elif ep is False:
        add("endpoint", -8, "נקודת הסיום שונה מהשלב הקודם")
    pop = f.get("population_consistent")
    if pop is True:
        add("population", 4, "אוכלוסיית המטופלים זהה לשלב הקודם")
    elif pop == "partial":
        add("population", 0, "אוכלוסייה דומה חלקית (ללא בונוס)")
    elif pop is False:
        add("population", -8, "אוכלוסיית המטופלים שונה מהשלב הקודם")
    if f.get("regimen_changed") is True:
        add("regimen", -4, "משטר מינון/טיפול שונה מהשלב הקודם")
    if f.get("design") in DESIGN_PTS:
        add("design", DESIGN_PTS[f["design"]], f"תכנון: {DESIGN_HE[f['design']]}")
    if f.get("objective") is True:
        add("objective", 3, "נקודת סיום אובייקטיבית")
    elif f.get("objective") is False:
        add("objective", -3, "נקודת סיום תלויה בהערכה/סובייקטיבית")
    mv = f.get("mechanism_validated")
    if mv is True:
        add("mechanism", 6, "המנגנון מאומת קלינית (תרופות מאושרות)")
    elif mv == "partial":
        add("mechanism", 2, "המנגנון מאומת חלקית")
    elif mv is False:
        add("mechanism", -3, "מנגנון חדש/לא מאומת")
    if f.get("class_failures"):
        add("class_failures", -4, "כישלונות קודמים באותו מנגנון")
    if f.get("safety_concern") is True:
        add("safety", -6, "אות בטיחות ידוע")
    if f.get("n") is not None and f["n"] < 100:
        add("sample", -4, f"מדגם קטן (n={f['n']})")
    for r in f.get("reg") or ():
        pts = {"SPA": 4, "BTD": 3, "RMAT": 2}.get(r)
        if pts:
            add("regulatory", pts, f"ייעוד רגולטורי: {r}")
    if f.get("prior_crl"):
        add("prior_crl", -6, "CRL קודם לתוכנית")
    if f.get("negative_adcom"):
        add("negative_adcom", -30, "הצבעה שלילית בוועדה מייעצת (אומדן: מוריד משמעותית את סיכוי האישור)")
    if f.get("fda_requirement_at_risk"):
        add("fda_requirement", -25, "ה-FDA הגדיר דרישה שהנתונים אינם עומדים בה באופן ברור")
    if f.get("integrity"):
        add("integrity", -15, "חששות לשלמות הנתונים/המחקר")
    score = max(5, min(95, sum(x["points"] for x in c)))
    known = sum(1 for k in EVIDENCE_CHECKS if f.get(k) is not None)
    completeness = known / len(EVIDENCE_CHECKS)
    p_mid = max(0.03, min(0.97, base + (score / 100 - base) * 0.6))
    band = 0.12 + (1 - completeness) * 0.15
    return {
        "score": score, "category": evidence_category(score), "contributions": c, "base_rate": base,
        "completeness": completeness,
        "model_p": {"p": p_mid, "lo": max(0.01, p_mid - band), "hi": min(0.99, p_mid + band),
                    "label": "model estimate — uncalibrated heuristic, NOT an objective probability"},
        "version": VERSIONS["clinical_evidence"],
    }


def _legacy_risk_flags(s):
    out = [dict(f, origin="source") for f in s.get("flags", ())]
    f = s.get("features") or {}

    def add(sev, code, text):
        out.append({"sev": sev, "code": code, "text": text, "origin": "computed"})

    if f.get("integrity"):
        add("critical", "integrity", "חששות לשלמות נתונים/מחקר")
    if f.get("negative_adcom"):
        add("critical", "negative_adcom", "הצבעה שלילית בוועדה מייעצת")
    if f.get("prior_crl"):
        add("high", "prior_crl", "CRL קודם לתוכנית")
    if f.get("population_consistent") is False:
        add("high", "population_changed", "אוכלוסיית המחקר שונה מהשלב הקודם")
    if f.get("endpoint_consistent") is False:
        add("medium", "endpoint_changed", "נקודת הסיום שונה מהשלב הקודם")
    if f.get("regimen_changed"):
        add("medium", "regimen_changed", "משטר המינון שונה מהשלב הקודם")
    if f.get("design") in ("single_arm", "external_control"):
        add("medium", "uncontrolled", "ללא ביקורת אקראית")
    if f.get("safety_concern"):
        add("high", "safety", "אות בטיחות ידוע")
    if f.get("n") is not None and f["n"] < 100:
        add("medium", "small_n", "מדגם קטן")
    if f.get("class_failures"):
        add("medium", "class_failures", "כישלונות קודמים במנגנון")
    rw = s.get("runway_months")
    if rw is not None and rw < 12:
        add("high", "runway", f"מסלול מזומנים ~{rw} חודשים (<12) — סיכון גיוס/דילול")
    if s.get("mcap") == "micro":
        add("medium", "liquidity", "מיקרו-קאפ — סיכון נזילות ומרווחים")
    seen, uniq = set(), []
    for x in sorted(out, key=lambda x: SEV_ORDER.get(x["sev"], 9)):
        if x["code"] not in seen:
            seen.add(x["code"])
            uniq.append(x)
    return uniq


def classify(impact, evidence, flags, scenario, date_conf, market_data_available, days_to):
    v = VERSIONS["classifier"]
    crit = [f for f in flags if f["sev"] == "critical"]
    if crit:
        return {"cls": "AVOID", "reasons": ["דגל סיכון קריטי: " + "; ".join(f["text"] for f in crit)], "version": v}
    if evidence["score"] < 45:
        return {"cls": "AVOID", "reasons": ["ראיות קליניות חלשות (<45)"], "version": v}
    scen_ok = bool(scenario and scenario.get("available"))
    if scen_ok and scenario["bear_mid"] < -0.6 and evidence["score"] < 70:
        return {"cls": "AVOID", "reasons": ["תרחיש דוב חמור (< -60%) עם ראיות שאינן חזקות מספיק (<70)"], "version": v}
    high = any(f["sev"] == "high" for f in flags)
    asym = scenario.get("asymmetry") if scen_ok else None
    if (evidence["score"] >= 75 and asym and asym >= 2 and date_conf >= 80 and market_data_available and not high):
        return {"cls": "HOLD", "reasons": ["ראיות ≥75, אסימטריה ≥2, ביטחון תאריך ≥80, נתוני שוק זמינים, ללא דגלים גבוהים"], "version": v}
    if (RUNUP_EDGE_VALIDATED and impact["score"] >= 70 and days_to is not None and days_to >= 14 and date_conf >= 55):
        return {"cls": "RUNUP", "reasons": ["השפעה גבוהה, ≥14 יום לאירוע, ומודל ריצה-מקדימה אומת"], "version": v}
    reasons = []
    if not market_data_available:
        reasons.append("אין נתוני מחיר/אופציות — לא ניתן להעריך מה כבר מתומחר")
    if not RUNUP_EDGE_VALIDATED:
        reasons.append("מודל הריצה המקדימה לא אומת מחוץ למדגם")
    if evidence["score"] < 75:
        reasons.append("ראיות מתחת לסף החזקה (75)")
    if high:
        reasons.append("קיימים דגלי סיכון גבוהים")
    if not scen_ok:
        reasons.append("אין מספיק היסטוריה לבניית תרחישים")
    return {"cls": "WATCH", "reasons": reasons, "version": v}


# Frozen regression surface; the active v2 engine imports scoring_common directly.
catalyst_impact = _legacy_catalyst_impact
from .scoring_common import risk_flags  # noqa: E402
