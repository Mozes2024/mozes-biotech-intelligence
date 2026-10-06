"""Fast deterministic materiality / outcome classification for Stage-1 alerts.

LLM enrichment is optional and must never block the first push.
Regression corpus: tests/fixtures/headline_corpus.tsv.
"""
from __future__ import annotations

import re

_PRIMARY = r"(?:its\s+|the\s+|both\s+|all\s+)?(?:co-?)?primary(?:\s+efficacy)?\s+(?:and\s+(?:key\s+)?secondary\s+)?end\s*-?points?"

POSITIVE = re.compile(
    r"\b(?:(?:met|meets|meeting|achieved|achieves|achieving|hit|hits)\s+" + _PRIMARY + r"|"
    r"statistically\s+significant|"
    r"positive\s+(?:top[ -]?line|interim|pivotal|phase|final|data|results|chmp\s+opinion|opinion)|"
    r"(?:fda|ema|european\s+commission|health\s+canada|mhra|pmda)\s+(?:has\s+)?(?:approv\w+|grants?\s+(?:full\s+|accelerated\s+)?approval)"
    r"(?!\s+(?:decision|date|process|pathway|timeline|submission|application))|"
    r"approved\s+by\s+the\s+(?:u\.s\.\s+)?(?:fda|food\s+and\s+drug\s+administration)|"
    r"(?:receives?|received|granted|wins?)\s+(?:u\.s\.\s+)?(?:fda\s+)?(?:full\s+|accelerated\s+|conditional\s+)?(?:marketing\s+)?approval|"
    r"accelerated\s+approval|"
    r"definitive\s+agreement\s+to\s+be\s+acquired|to\s+be\s+acquired\s+by|"
    r"accepted\s+for\s+(?:priority\s+)?review|"
    r"(?:lifts?|lifted|removes?|removed|releases?|released)\s+(?:the\s+|its\s+|partial\s+|full\s+)*clinical\s+hold|"
    r"clinical\s+hold\b[^.;]{0,40}?\b(?:lifted|removed|released)|"
    r"vot(?:es?|ed)\s+(?:\d+\s*(?:-|to)\s*\d+\s+)?in\s+favou?r)\b",
    re.I,
)
NEGATIVE = re.compile(
    r"\b(?:(?:did|does|do)\s+not\s+(?:meet|achieve|hit)|missed|misses)\s+" + _PRIMARY + r"|"
    r"\b(?:failed|fails)\s+to\s+(?:meet|achieve|demonstrate|show|reach)\b|"
    r"\b(?:did|does)\s+not\s+(?:reach|achieve)\s+statistical\s+significance\s+(?:on|for)\s+" + _PRIMARY + r"|"
    r"\bnot\s+statistically\s+significant\b|"
    r"\bvot(?:es?|ed)\s+(?:\d+\s*(?:-|to)\s*\d+\s+)?against\b|"
    r"\bcomplete\s+response\s+letter\b|\bCRL\b|"
    r"\b(?:fda\s+)?(?:places?|placed|imposes?|imposed|issues?|issued)\s+(?:a\s+)?(?:partial\s+|full\s+)?clinical\s+hold\b|"
    r"\bclinical\s+hold\b(?![^.;]{0,40}?\b(?:lifted|removed|released|resolved)\b)|"
    r"\b(?:stopped|halted|terminated|discontinued|ended)\s+(?:early\s+)?(?:for|due\s+to)\s+futility\b|"
    r"\bfutility\s+(?:boundary|threshold|criteria|criterion)\s+(?:was\s+|were\s+)?(?:crossed|met|reached)\b|"
    r"\b(?:discontinu(?:e|es|ed|ing)|terminat(?:e|es|ed|ing)|halt(?:s|ed|ing)?)\s+(?:the\s+|its\s+|further\s+|all\s+)?"
    r"(?:development|program|programme|trial|study|phase\s*[123]\w*|clinical\s+development)\b|"
    r"\bdiscontinuation\s+of\s+(?:the\s+|its\s+)?(?:development|program|programme|trial|study)\b|"
    r"\bpdufa\s+(?:target\s+action\s+)?(?:date\s+)?(?:extended|extension)\b|\bextends?\s+(?:the\s+)?pdufa\b|"
    r"\brefus(?:e|ed|al)\s+to\s+file\b|"
    r"\bwithdr(?:aw|aws|ew|awn)\s+(?:its\s+|the\s+)?(?:nda|bla|maa|marketing\s+application|application)\b",
    re.I,
)
MIXED = re.compile(
    r"\b(numerical\s+trend|did\s+not\s+(?:reach|achieve)\s+statistical\s+significance|"
    r"secondary\s+endpoints?\s+only|mixed\s+(?:results|data))\b",
    re.I,
)
MATERIAL_HEADLINE = re.compile(
    r"\b(top[ -]?line|read[ -]?out|pdufa|adcom|"
    r"advisory\s+committee|fda\s+(?:decision|approv\w*|briefing)|"
    r"primary\s+endpoint|interim\s+(?:\w+\s+)?analysis|futility|clinical\s+hold|complete\s+response|"
    r"(?:results|data)\s+(?:from|of)\s+(?:the\s+|its\s+)?(?:pivotal\s+|registrational\s+)?phase\s*[23]|"
    r"halt|news\s+pending)\b",
    re.I,
)
# Within the same clause before a positive/negative phrase, these words mean the outcome
# has not happened ("seeking FDA approval", "no clinical hold", "not statistically significant").
_HEDGE = (r"not|no|non|without|never|fail\w*|seek\w*|plan\w*|intend\w*|potential(?:ly)?|support(?:s|ing)?|toward\w*|"
          r"pursu\w*|submit\w*|submission|filing|file|apply|application\s+for|anticipat\w*|expect\w*|"
          r"could|might|would|path(?:way)?\s+to|ahead\s+of|prior\s+to|pending|await\w*|whether|if|"
          r"hop\w*|aim\w*|designed\s+to|enable\w*")
_GUARD = re.compile(r"\b(?:" + _HEDGE + r")\b[-\w\s,']{0,25}$", re.I)
# A negative term that is being cleared or answered is not a new negative outcome.
_NEGATIVE_GUARD = re.compile(
    r"\b(?:" + _HEDGE + r"|following|after|address\w*|resolv\w*|resubmi\w*|previously|prior|"
    r"lifts?|lifted|removes?|removed|releases?|released|passe[sd]|pass|cleared|clears|survived)\b[-\w\s,']{0,25}$",
    re.I,
)
_SCHEDULING = re.compile(
    r"\b(?:to|will)\s+(?:present|host|participate|report|webcast|showcase|announce|hold|share)\b|"
    r"\b(?:conference\s+call|webcast|investor\s+day|r&d\s+day|kol\s+event|fireside)\b",
    re.I,
)

ALERTABLE_CHANGE_TYPES = frozenset({
    "sec_material_filing", "sec_filing_signal", "company_release_signal",
    "news_signal", "wire_release_signal", "nasdaq_halt_signal", "nasdaq_volatility_pause",
    "fda_release_signal",
    "ctgov_status_changed", "ctgov_why_stopped_changed",
    "ctgov_primary_completion_type_changed", "ctgov_results_first_posted_changed",
})
PRIORITY_RANK = {"P1": 1, "P2": 2, "P3": 3}
# Headline-only releases: without a material outcome they are browsing, not an alert.
RELEASE_TYPES = frozenset({"company_release_signal", "wire_release_signal", "fda_release_signal", "news_signal"})


def _clause_before(text, start):
    window = text[max(0, start - 80):start]
    return re.split(r"[.;:!?\n]|\s[-–—]\s", window)[-1]


def _affirmed(pattern, text, guard):
    for match in pattern.finditer(text):
        if not guard.search(_clause_before(text, match.start())):
            return True
    return False


def classify_outcome(text: str) -> dict:
    """Return polarity for the first paragraphs/headline only."""
    sample = (text or "")[:4000]
    positive = _affirmed(POSITIVE, sample, _GUARD)
    negative = _affirmed(NEGATIVE, sample, _NEGATIVE_GUARD)
    if (positive and negative) or MIXED.search(sample) and not negative:
        polarity = "mixed"
    elif negative:
        polarity = "negative"
    elif positive:
        polarity = "positive"
    else:
        polarity = "unknown"
    material = polarity != "unknown" or (
        _affirmed(MATERIAL_HEADLINE, sample, _GUARD) and not _SCHEDULING.search(sample[:300]))
    return {
        "polarity": polarity,
        "material": material,
        "method": "deterministic_regex_v2",
        "uncalibrated": True,
    }


def alert_priority(change_type: str, severity: str, outcome: dict | None = None, *,
                   ticker: str | None = None, watched: bool = False) -> str:
    """P1 is reserved for issuers on the watchlist; unattributed signals never page."""
    outcome = outcome or {}
    if not ticker:
        return "P3"
    if watched and (change_type == "nasdaq_halt_signal" or severity == "critical"):
        return "P1"
    if watched and change_type in {"sec_material_filing", "company_release_signal", "fda_release_signal",
                                   "wire_release_signal"} and outcome.get("material"):
        return "P1"
    if change_type in RELEASE_TYPES and not outcome.get("material"):
        return "P3"
    if severity in {"critical", "high", "medium"}:
        return "P2"
    return "P3"


def should_enqueue(change_type: str, severity: str) -> bool:
    return change_type in ALERTABLE_CHANGE_TYPES and severity in {"medium", "high", "critical"}
