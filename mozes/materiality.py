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
    r"(?:fda|ema|european\s+commission|health\s+canada|mhra|pmda)\s+(?:has\s+)?(?:approv(?:es|ed)|grants?\s+(?:full\s+|accelerated\s+)?approval)"
    r"(?!\s+(?:decision|date|process|pathway|timeline|submission|application))|"
    r"approved\s+by\s+the\s+(?:u\.s\.\s+)?(?:fda|food\s+and\s+drug\s+administration)|"
    r"(?:receives?|received|granted|wins?)\s+(?:u\.s\.\s+)?(?:fda\s+)?(?:full\s+|accelerated\s+|conditional\s+)?(?:marketing\s+)?approval|"
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
    r"phase\s*[23]\b.{0,30}\bresults\b|"
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
    "clinical_catalyst_signal",
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
    # Keep the entire subject and conditional antecedent. Commas do not end
    # a scope: "If ... , the FDA ..." remains conditional.
    return re.split(r"[;!?]|(?<!\bU)(?<!\bS)\.\s+|\b(?:but|however|whereas)\b",
                    text[:start], flags=re.I)[-1]


def _affirmed(pattern, text, guard):
    for match in pattern.finditer(text):
        before = _clause_before(text, match.start())
        if guard is _GUARD and re.search(r"primary|phase|top[ -]?line|statistically", match.group(), re.I):
            # An application seeking approval AFTER completed trial results does
            # not make those results prospective. The application and outcome
            # are separate assertions.
            before = re.split(r"\b(?:after|following)\b", before, flags=re.I)[-1]
        # A guard token governs this clause, irrespective of subject length.
        scope = r"\b(?:" + _HEDGE + r"|may|subject\s+to|historically|previously|formerly)\b"
        if guard is _NEGATIVE_GUARD:
            scope = r"\b(?:" + _HEDGE + r"|may|following|after|address\w*|resolv\w*|resubmi\w*|previously|prior|lifts?|lifted|removes?|removed|cleared)\b"
        after = re.split(r"[;!?]|\.\s+", text[match.end():], maxsplit=1)[0]
        background = re.search(r"\b(?:in|since)\s+(?:19|20)\d{2}\b|\b(?:historically|previously|last year)\b", before + " " + after, re.I)
        prospective = pattern is not MATERIAL_HEADLINE and re.search(r"^\s*(?:is|was|remains)?\s*(?:expected|pending|anticipated|possible|subject to)\b", after, re.I)
        if not background and not prospective and not re.search(scope, before, re.I) and not guard.search(before):
            return True
    return False


def classify_outcome(text: str) -> dict:
    """Return polarity for the first paragraphs/headline only."""
    sample = re.sub(r"\s+", " ", re.split(r"\b(?:forward-looking statements|safe harbor statement|about the company)\b",
                      text or "", maxsplit=1, flags=re.I)[0])[:12_000]
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
    cvr = bool(re.search(r"\b(?:contingent value rights?|CVRs?)\b", sample, re.I))
    approval = _affirmed(re.compile(r"\b(?:FDA\s+(?:has\s+)?(?:approv(?:es|ed)|grants?\s+(?:full\s+|accelerated\s+)?approval)|approved\s+by\s+the\s+(?:U\.S\.\s+)?FDA|(?:receives?|received|granted)\s+(?:FDA\s+)?(?:full\s+|accelerated\s+)?approval)\b", re.I), sample, _GUARD)
    hold = _affirmed(re.compile(r"\bclinical\s+hold\b(?![^.;]{0,40}\b(?:lifted|removed|released|resolved)\b)", re.I), sample, _NEGATIVE_GUARD)
    clinical_positive = _affirmed(re.compile(
        r"\b(?:(?:met|meets|achieved)\s+" + _PRIMARY +
        r"|statistically\s+significant|positive\s+(?:top[ -]?line|interim|pivotal|phase|final|data|results))\b", re.I), sample, _GUARD)
    clinical_negative = _affirmed(re.compile(
        r"\b(?:(?:did|does)\s+not\s+(?:meet|achieve)\s+" + _PRIMARY +
        r"|(?:failed to meet|missed)\s+" + _PRIMARY +
        r"|not statistically significant|(?:stopped|halted|terminated)\s+(?:for|due to)\s+futility)\b", re.I), sample, _NEGATIVE_GUARD)
    clinical = clinical_positive or clinical_negative
    crl = _affirmed(re.compile(r"\b(?:CRL|complete response letter|refusal to file)\b", re.I), sample, _NEGATIVE_GUARD)
    milestone = next((name for name, pattern in (
        ("review_acceptance", r"\baccepted for (?:priority )?review\b"),
        ("designation", r"\b(?:received|receives|granted)\b[^.;]{0,100}\bdesignation\b"),
        ("submission", r"\b(?:submitted|submits)\b[^.;]{0,100}\b(?:NDA|BLA|marketing application)\b"))
        if _affirmed(re.compile(pattern, re.I), sample, _GUARD)), None)
    family = ("material_safety" if hold else "fda_decision" if approval or crl else
              "clinical_outcome" if clinical and polarity != "unknown" else "corporate_action" if cvr else
              "merger_acquisition" if re.search(r"\b(?:definitive agreement to be acquired|merger agreement|to be acquired by)\b", sample, re.I) else
              "financing" if re.search(r"\b(?:public offering|private placement|registered direct|financing)\b", sample, re.I) else
              "regulatory_milestone" if milestone else
              "conditional_milestone" if re.search(r"\b(?:FDA|approval|PRV)\b", sample, re.I) else "routine_administration")
    if cvr and not (approval or crl or hold or clinical and polarity != "unknown"):
        polarity, material = "unknown", True
    material = material or family in {"merger_acquisition", "financing"}
    return {
        "polarity": polarity,
        "material": material,
        "event_family": family,
        "event_outcome": "conditional_cvr" if family == "corporate_action" else
                         "approval" if approval else "clinical_hold" if hold else "rejection" if crl else
                         milestone if milestone and family == "regulatory_milestone" else
                         "confirmed" if polarity != "unknown" else "explicit_absence" if re.search(r"\b(?:no|not|never|without)\b[^.;]*\bapprov\w*\b", sample, re.I) else "unconfirmed",
        "urgent": family in {"fda_decision", "material_safety", "clinical_outcome"} and polarity != "unknown",
        "method": "deterministic_clauses_v3",
        "uncalibrated": True,
    }


def alert_priority(change_type: str, severity: str, outcome: dict | None = None, *,
                   ticker: str | None = None, watched: bool = False,
                   verified: bool = False) -> str:
    """Urgency and verified identity are independent of monitoring preference."""
    outcome = outcome or {}
    if not ticker:
        return "P3"
    if change_type == "clinical_catalyst_signal" or outcome.get("actionable") is False:
        return "P2" if outcome.get("material") else "P3"
    if watched and change_type == "nasdaq_halt_signal":
        return "P1"
    if verified and outcome.get("urgent") and outcome.get("material"):
        return "P1"
    if outcome.get("event_family") == "corporate_action":
        return "P2" if outcome.get("material") else "P3"
    if outcome.get("event_family") == "routine_administration" and not outcome.get("material"):
        return "P3"
    if change_type in RELEASE_TYPES and not outcome.get("material"):
        return "P3"
    if severity in {"critical", "high", "medium"}:
        return "P2"
    return "P3"


def should_enqueue(change_type: str, severity: str) -> bool:
    return change_type in ALERTABLE_CHANGE_TYPES and severity in {"medium", "high", "critical"}
