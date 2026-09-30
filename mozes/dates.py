"""Date-window normalization for catalyst guidance text.

Never fabricates precision: the window width always reflects what the source said.
"Q4 2026" -> 2026-10-01..2026-12-31 (quarter), "December 14, 2026" -> exact, etc.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

PRECISION_CONF = {
    "exact": 95, "month": 75, "quarter": 55, "mid_year": 40,
    "early_late_year": 35, "half": 35, "year": 20, "relative": 10, "unknown": 0,
}
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MRE = (r"(january|february|march|april|may|june|july|august|september|october|november|december"
       r"|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?")
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _mi(s: str) -> int:
    return _MONTHS.index(s[:3]) + 1


def last_day(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days


def pub_effective(d: str | None) -> str:
    """Conservative effective publication date. Month/year precision -> END of the period,
    so a document known only as '2026-09' is never treated as available on 2026-09-15."""
    if not d:
        return "9999-12-31"
    if len(d) == 4:
        return f"{d}-12-31"
    if len(d) == 7:
        y, m = map(int, d.split("-"))
        return last_day(y, m).isoformat()
    return d[:10]


@dataclass(frozen=True)
class DateWindow:
    original: str
    precision: str
    start: str | None
    end: str | None
    confidence: int
    inferred_year: bool = False


def normalize_date_text(text, ref: date, confirmed_by_company=False, secondary_source=False) -> DateWindow:
    t = str(text).lower()
    ry = ref.year

    def out(p, s=None, e=None, inferred=False):
        c = PRECISION_CONF[p]
        if p == "exact" and confirmed_by_company and not inferred:
            c = 100
        if inferred:
            c -= 10
        if secondary_source:
            c -= 15
        return DateWindow(str(text), p, s.isoformat() if s else None, e.isoformat() if e else None, max(0, c), inferred)

    def quarter(q, y):
        m0 = 3 * (q - 1) + 1
        return out("quarter", date(y, m0, 1), last_day(y, m0 + 2))

    def half(h, y):
        if h == 1:
            return out("half", date(y, 1, 1), date(y, 6, 30))
        return out("half", date(y, 7, 1), date(y, 12, 31))

    if m := re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", t):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return out("exact", d, d)
    if m := re.search(rf"\b{MRE}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d{{2}})\b", t):
        d = date(int(m[3]), _mi(m[1]), int(m[2]))
        return out("exact", d, d)
    if m := re.search(rf"\b{MRE},?\s+(20\d{{2}})\b", t):
        y, mo = int(m[2]), _mi(m[1])
        return out("month", date(y, mo, 1), last_day(y, mo))
    if m := re.search(r"\b(?:q([1-4])|([1-4])q)\s*[-']?\s*(20\d{2})\b", t):
        return quarter(int(m[1] or m[2]), int(m[3]))
    if m := re.search(r"\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?(20\d{2})\b", t):
        return quarter(ORD[m[1]], int(m[2]))
    if m := re.search(r"\b(?:h([12])|([12])h)\s*(20\d{2})\b", t):
        return half(int(m[1] or m[2]), int(m[3]))
    if m := re.search(r"\b(first|second)\s+half\s+(?:of\s+)?(?:the\s+year\s+)?(20\d{2})\b", t):
        return half(ORD[m[1]], int(m[2]))
    if m := re.search(r"\bmid[- ]?(20\d{2})\b", t):
        y = int(m[1])
        return out("mid_year", date(y, 5, 1), date(y, 8, 31))
    if m := re.search(r"\b(early|late)[- ]?(20\d{2})\b", t):
        y = int(m[2])
        if m[1] == "early":
            return out("early_late_year", date(y, 1, 1), date(y, 4, 30))
        return out("early_late_year", date(y, 9, 1), date(y, 12, 31))
    if m := re.search(r"\b(?:end of|year[- ]end)\s+(20\d{2})\b", t):
        y = int(m[1])
        return out("early_late_year", date(y, 9, 1), date(y, 12, 31))
    if re.search(r"\b(?:year[- ]end|end of (?:the )?year)\b", t):
        return out("early_late_year", date(ry, 9, 1), date(ry, 12, 31))
    if m := re.search(rf"\b{MRE}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", t):
        try:
            d = date(ry, _mi(m[1]), int(m[2]))
            return out("exact", d, d, inferred=True)
        except ValueError:
            pass
    if "next year" in t:
        return out("relative", date(ry + 1, 1, 1), date(ry + 1, 12, 31))
    if "coming weeks" in t:
        return out("relative", ref, ref + timedelta(days=42))
    if "coming months" in t:
        return out("relative", ref, ref + timedelta(days=120))
    if "this year" in t:
        return out("relative", ref, date(ry, 12, 31))
    if m := re.search(r"\b(20\d{2})\b", t):
        y = int(m[1])
        return out("year", date(y, 1, 1), date(y, 12, 31))
    return out("unknown")


def date_info(chronology, sources):
    """Builds the catalyst timeline, current expected window, and detected date changes."""
    timeline = []
    for c in chronology:
        c = dict(c)
        item = {k: c.get(k) for k in ("date", "text", "src", "date_text", "superseded")}
        if c.get("date_text"):
            rel = (sources.get(c.get("src")) or {}).get("reliability")
            w = normalize_date_text(
                c["date_text"], date.fromisoformat(pub_effective(c["date"])),
                confirmed_by_company=(rel == "primary"), secondary_source=(rel != "primary"),
            )
            item["norm"] = asdict(w)
        timeline.append(item)
    timeline.sort(key=lambda x: pub_effective(x["date"]))
    dated = [x for x in timeline if x.get("norm") and x["norm"]["precision"] != "unknown"]
    changes = []
    for prev, cur in zip(dated, dated[1:]):
        pn, cn = prev["norm"], cur["norm"]
        if pn["end"] and cn["end"] and cn["end"] > pn["end"]:
            changes.append({"type": "delay", "from": pn["original"], "to": cn["original"], "date": cur["date"], "src": cur.get("src")})
        elif pn["start"] and cn["end"] and cn["end"] < pn["start"]:
            changes.append({"type": "pulled_forward", "from": pn["original"], "to": cn["original"], "date": cur["date"], "src": cur.get("src")})
        if cn["confidence"] > pn["confidence"]:
            changes.append({"type": "precision_up", "from": pn["original"], "to": cn["original"], "date": cur["date"], "src": cur.get("src")})
    current = [x for x in dated if not x.get("superseded")]
    if not current:
        return {"window": None, "precision": None, "confidence": 0, "original": None, "changes": changes, "timeline": timeline}
    n = current[-1]["norm"]
    return {"window": {"start": n["start"], "end": n["end"]}, "precision": n["precision"], "confidence": n["confidence"],
            "original": n["original"], "changes": changes, "timeline": timeline}
