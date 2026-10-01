"""Deterministic catalyst-statement extraction (works without any AI provider)."""
from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date

from .dates import normalize_date_text
from .timing import parse_trigger, PROGRESS

CATALYST_KW = re.compile(
    r"(topline|top-line|results expected|readout|pivotal|registrational|phase\s?(?:3|iii|2b|2|ii|1/2)"
    r"|interim analysis|primary endpoint|pdufa|\bbla\b|\bnda\b|advisory committee|adcom"
    r"|data presentation|late-breaking|target action date)",
    re.I,
)
SPLIT = re.compile(r"\n+|(?<=[.!?])\s+")


def classify_statement(s: str) -> str:
    low = s.lower()
    if re.search(r"pdufa|target action date", low):
        return "PDUFA"
    if re.search(r"advisory committee|adcom", low):
        return "ADCOM"
    if re.search(r"(submit|submission|file|filing)\w*.{0,40}\b(bla|nda)\b|\b(bla|nda)\b.{0,40}(submission|filing)", low):
        return "FILING"
    if "interim" in low:
        return "INTERIM"
    if re.search(r"present|late-breaking|conference|medical meeting", low):
        return "CONFERENCE"
    if re.search(r"phase\s?(3|iii)\b", low):
        return "P3_TOPLINE"
    if re.search(r"phase\s?(2b|2|ii)\b", low):
        return "P2_TOPLINE"
    if re.search(r"topline|top-line|results|readout|data", low):
        return "READOUT"
    return "OTHER"


def extract_catalyst_statements(text: str, ref: date, source_id=None, reliability="primary"):
    """ref = publication date of the document (relative expressions resolve against it)."""
    out = []
    # Preserve independent calendar statements. Only attach an adjacent progress
    # sentence to a trigger, never an unrelated catalyst or financial statement.
    spans = []
    for paragraph in re.split(r"\n+", text or ""):
        paragraph_start = len(spans)
        sentences = SPLIT.split(paragraph)
        for sent in sentences:
            if (len(spans) > paragraph_start and PROGRESS.search(sent) and not CATALYST_KW.search(sent)
                    and parse_trigger(spans[-1]) and len(spans[-1]) + len(sent) < 2000):
                spans[-1] += " " + sent
            else:
                spans.append(sent)
    for sent in spans:
        s = sent.strip()
        trigger = parse_trigger(s)
        if not s or not (CATALYST_KW.search(s) or trigger):
            continue
        w = normalize_date_text(s, ref, confirmed_by_company=(reliability == "primary"),
                                secondary_source=(reliability != "primary"))
        if w.precision == "unknown" and not trigger:
            continue
        window = asdict(w)
        if trigger:
            window.update(start=None, end=None, precision="unknown")
        out.append({"statement": s, "catalyst_type": classify_statement(s), "window": window,
                    "source_id": source_id, "source_url": source_id, "reliability": reliability,
                    "published_at": ref.isoformat(), "timing_mode": "CALENDAR",
                    **(trigger or {})})
    return out
