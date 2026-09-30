"""Countdown + change alerts with basic noise control."""
from .dates import days_between, pub_effective

LEVELS = ((3, "critical"), (7, "high"), (14, "medium"), (30, "low"), (60, "info"))
LEVEL_W = {"critical": 40, "high": 30, "in_window": 25, "medium": 20, "low": 10, "info": 5, "passed": 0}


def build_alerts(analyses, today, change_lookback_days=45):
    out = []
    for a in analyses:
        s, w = a["snapshot"], a["date"]["window"]
        base = {"event_id": a["id"], "ticker": s["ticker"], "program": s["program"], "type": s["type"],
                "impact": a["impact"]["score"], "evidence": a["evidence"]["score"],
                "evidence_category": a["evidence"]["category"], "date_confidence": a["date"]["confidence"],
                "cls": a["classification"]["cls"], "window": w,
                "top_flags": [f["text"] for f in a["flags"][:2]]}
        if w and w.get("start"):
            ds, de = days_between(today, w["start"]), days_between(today, w["end"])
            if de < 0:
                level = "passed"
            elif ds <= 0:
                level = "in_window"
            else:
                level = next((lv for th, lv in LEVELS if ds <= th), None)
            if level and not (base["impact"] < 45 and level in ("info", "low")):
                out.append({**base, "kind": "countdown", "level": level, "days_to_start": ds,
                             "prominence": round(0.5 * base["impact"] + 0.3 * base["date_confidence"] + LEVEL_W[level], 1)})
        for ch in a["date"]["changes"]:
            if days_between(pub_effective(ch["date"]), today) <= change_lookback_days:
                out.append({**base, "kind": "change", "level": ch["type"], "change": ch,
                            "prominence": round(0.5 * base["impact"] + 20, 1)})
    return sorted(out, key=lambda x: -x["prominence"])
