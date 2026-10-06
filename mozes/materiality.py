"""Fast deterministic materiality / outcome classification for Stage-1 alerts.

LLM enrichment is optional and must never block the first push.
"""
from __future__ import annotations

import re

POSITIVE = re.compile(
    r"\b(met(?:\s+its)?\s+primary\s+endpoint|statistically\s+significant|"
    r"fda\s+approv\w*|accelerated\s+approval|positive\s+topline|"
    r"definitive\s+agreement\s+to\s+be\s+acquired|accepted\s+for\s+(?:priority\s+)?review)\b",
    re.I,
)
NEGATIVE = re.compile(
    r"\b(did\s+not\s+meet|failed\s+to|complete\s+response\s+letter|\bcrl\b|"
    r"clinical\s+hold|futility|discontinu\w*|pdufa\s+extended|refuse(?:d)?\s+to\s+file)\b",
    re.I,
)
MIXED = re.compile(
    r"\b(numerical\s+trend|did\s+not\s+reach\s+statistical\s+significance|"
    r"secondary\s+endpoints?\s+only|mixed\s+(?:results|data))\b",
    re.I,
)
MATERIAL_HEADLINE = re.compile(
    r"\b(phase\s*[23](?:\s*/\s*3)?|top[ -]?line|read[ -]?out|pdufa|adcom|"
    r"advisory\s+committee|fda\s+(?:decision|approv\w*|briefing)|"
    r"primary\s+endpoint|interim\s+analysis|clinical\s+hold|complete\s+response|"
    r"8-?k|6-?k|halt|news\s+pending)\b",
    re.I,
)

ALERTABLE_CHANGE_TYPES = frozenset({
    "sec_material_filing", "sec_filing_signal", "company_release_signal",
    "news_signal", "wire_release_signal", "nasdaq_halt_signal", "fda_release_signal",
    "ctgov_status_changed", "ctgov_why_stopped_changed",
    "ctgov_primary_completion_type_changed", "ctgov_results_first_posted_changed",
})


def classify_outcome(text: str) -> dict:
    """Return polarity for the first paragraphs/headline only."""
    sample = (text or "")[:4000]
    if NEGATIVE.search(sample):
        polarity = "negative"
    elif POSITIVE.search(sample):
        polarity = "positive"
    elif MIXED.search(sample):
        polarity = "mixed"
    else:
        polarity = "unknown"
    return {
        "polarity": polarity,
        "material": bool(MATERIAL_HEADLINE.search(sample) or polarity != "unknown"),
        "method": "deterministic_regex_v1",
        "uncalibrated": True,
    }


def alert_priority(change_type: str, severity: str, outcome: dict | None = None) -> str:
    outcome = outcome or {}
    if change_type == "nasdaq_halt_signal" or severity == "critical":
        return "P1"
    if change_type in {"sec_material_filing", "company_release_signal", "fda_release_signal",
                       "wire_release_signal"} and outcome.get("material"):
        return "P1"
    if severity in {"high", "medium"}:
        return "P2"
    return "P3"


def should_enqueue(change_type: str, severity: str) -> bool:
    return change_type in ALERTABLE_CHANGE_TYPES and severity in {"medium", "high", "critical"}
