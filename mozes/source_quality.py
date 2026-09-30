"""Source quality, verification, and provenance rules.

ClinicalTrials.gov is discovery evidence, not proof of a readout date. Actionable event
verification requires a primary source such as SEC/company IR/FDA unless explicitly
quarantined for manual review.
"""
from __future__ import annotations

SOURCE_WEIGHTS = {
    "fda": 100,
    "sec": 98,
    "company_ir": 96,
    "company_press_release": 95,
    "federal_register": 95,
    "clinicaltrials": 68,
    "conference": 65,
    "peer_reviewed": 65,
    "secondary_calendar": 35,
    "news": 30,
    "aggregator": 20,
    "unknown": 10,
}

PRIMARY_TYPES = {
    "fda", "sec", "company_ir", "company_press_release", "federal_register"
}


def source_weight(source_type: str | None) -> int:
    return SOURCE_WEIGHTS.get((source_type or "unknown").lower(), SOURCE_WEIGHTS["unknown"])


def is_primary(source_type: str | None) -> bool:
    return (source_type or "").lower() in PRIMARY_TYPES


def best_source(sources: list[dict]) -> dict | None:
    if not sources:
        return None
    return max(sources, key=lambda s: (source_weight(s.get("source_type")), s.get("published_at") or ""))


def verification_state(sources: list[dict], date_precision: str | None = None) -> dict:
    """Return a conservative verification state for an event.

    VERIFIED means at least one primary source directly supports the event/date window.
    DISCOVERED means a registry or conference source supports a candidate only.
    QUARANTINED means only secondary/unknown sources support it.
    """
    b = best_source(sources)
    if not b:
        return {"state": "QUARANTINED", "confidence": 0, "reason": "no provenance"}
    st = (b.get("source_type") or "unknown").lower()
    w = source_weight(st)
    precision_bonus = {"exact": 4, "month": 2, "quarter": 0, "half": -5, "year": -10}.get(date_precision or "", -5)
    if is_primary(st):
        return {"state": "VERIFIED", "confidence": min(100, w + precision_bonus), "reason": f"primary source: {st}"}
    if st in {"clinicaltrials", "conference", "peer_reviewed"}:
        return {"state": "DISCOVERED", "confidence": max(0, min(85, w + precision_bonus)), "reason": f"discovery source only: {st}"}
    return {"state": "QUARANTINED", "confidence": max(0, min(55, w + precision_bonus)), "reason": f"secondary-only source: {st}"}
