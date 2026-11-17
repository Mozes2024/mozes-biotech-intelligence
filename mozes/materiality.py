"""Fast deterministic materiality / outcome classification for Stage-1 alerts.

LLM enrichment is optional and must never block the first push.
Regression corpus: tests/fixtures/headline_corpus.tsv.
"""
from __future__ import annotations

import re
from .alert_semantics import classify_evidence

MATERIAL_HEADLINE = re.compile(
    r"\b(top[ -]?line|read[ -]?out|pdufa|adcom|"
    r"advisory\s+committee|fda\s+(?:decision|approv\w*|briefing)|"
    r"primary\s+endpoint|interim\s+(?:\w+\s+)?analysis|futility|clinical\s+hold|complete\s+response|"
    r"(?:results|data)\s+(?:from|of)\s+(?:the\s+|its\s+)?(?:pivotal\s+|registrational\s+)?phase\s*[23]|"
    r"phase\s*[23]\b.{0,30}\bresults\b|"
    r"halt|news\s+pending)\b",
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


def classify_outcome(text: str, *, basis='headline_only', as_of=None) -> dict:
    """Return polarity for the first paragraphs/headline only."""
    return classify_evidence(text, basis, as_of=as_of)


def interruptive_outcome(outcome):
    allowed = {
        'fda_decision': {'approval', 'crl', 'pdufa_extension', 'refusal_to_file'},
        'material_safety': {'clinical_hold', 'hold_lifted'},
        'clinical_outcome': {'primary_endpoint_met', 'primary_endpoint_failed', 'primary_not_significant', 'futility', 'trial_discontinued'},
        'market_halt': {'halt', 'resumption', 'volatility_pause'},
    }
    return (outcome.get('semantic_version') == 'alert-evidence-v1' and
            outcome.get('material') is True and outcome.get('actionable') is not False and
            outcome.get('confirmed_outcome') is True and outcome.get('needs_review') is False and
            outcome.get('evidence_basis') in {'source_text', 'feed_summary', 'structured_source'} and
            outcome.get('event_outcome') in allowed.get(outcome.get('event_family'), set()))


def alert_priority(change_type: str, severity: str, outcome: dict | None = None, *,
                   ticker: str | None = None, watched: bool = False, issuer_verified=False) -> str:
    """Urgency requires identity and completed evidence, independent of preference."""
    outcome = outcome or {}
    if not ticker:
        return "P3"
    if change_type == "clinical_catalyst_signal" or outcome.get("actionable") is False:
        return "P2" if outcome.get("material") else "P3"
    if issuer_verified and interruptive_outcome(outcome) and outcome.get('material'):
        return "P1"
    if change_type in RELEASE_TYPES and not outcome.get("material"):
        return "P3"
    if severity in {"critical", "high", "medium"}:
        return "P2"
    return "P3"


def should_enqueue(change_type: str, severity: str) -> bool:
    return change_type in ALERTABLE_CHANGE_TYPES and severity in {"medium", "high", "critical"}
