"""Shared deterministic score components used by the active v2 engine.

Explanation identifiers are stable English API codes. Presentation layers translate them.
"""
from __future__ import annotations

from .versions import VERSIONS

TYPE_POINTS = {"P3_TOPLINE": 30, "P2_TOPLINE": 22, "P12_DATA": 20, "INTERIM": 20, "PDUFA_NME": 26, "PDUFA_GENERIC": 20, "ADCOM": 24, "PDUFA_SUPP": 12, "FILING": 8, "CONFERENCE": 10}
DEP_POINTS = {"single": 25, "lead": 18, "one_of_few": 12, "one_of_many": 5}
MCAP_POINTS = {"micro": 20, "small": 20, "mid": 16, "large": 10, "mega": 3, "unknown": 8}
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def catalyst_impact(event: dict) -> dict:
    contributions = []
    def add(code, points): contributions.append({"code": code, "points": points})
    kind = event.get("type")
    add("event_type", TYPE_POINTS.get(kind, 10))
    if event.get("pivotal") and kind in {"P2_TOPLINE", "P12_DATA"}: add("pivotal_designation", 4)
    add("program_dependency", DEP_POINTS.get(event.get("dependency"), 8))
    add("market_cap_band", MCAP_POINTS.get(event.get("mcap") or "unknown", 8))
    add("commercial_stage", 3 if event.get("commercial") else 10)
    if event.get("enables_filing"): add("filing_enabler", 10)
    if event.get("runway_months") is not None and event["runway_months"] < 12: add("short_cash_runway", 5)
    return {"score": min(100, sum(item["points"] for item in contributions)), "contributions": contributions, "version": VERSIONS["catalyst_impact"]}


def risk_flags(event: dict) -> list[dict]:
    flags = [dict(flag, origin="source") for flag in event.get("flags", ())]
    features = event.get("features") or {}
    def add(severity, code): flags.append({"sev": severity, "code": code, "origin": "computed"})
    if features.get("integrity"): add("critical", "data_integrity_concern")
    if features.get("negative_adcom"): add("critical", "negative_adcom")
    if features.get("prior_crl"): add("high", "prior_complete_response_letter")
    if features.get("population_consistent") is False: add("high", "population_changed")
    if features.get("endpoint_consistent") is False: add("medium", "endpoint_changed")
    if features.get("regimen_changed"): add("medium", "regimen_changed")
    if features.get("design") in {"single_arm", "external_control"}: add("medium", "uncontrolled_design")
    if features.get("safety_concern"): add("high", "safety_concern")
    if features.get("n") is not None and features["n"] < 100: add("medium", "small_sample")
    if features.get("class_failures"): add("medium", "class_failures")
    runway = event.get("runway_months")
    if runway is not None and runway < 12: add("high", "short_cash_runway")
    if event.get("mcap") == "micro": add("medium", "microcap_liquidity")
    seen, unique = set(), []
    for flag in sorted(flags, key=lambda item: SEV_ORDER.get(item.get("sev"), 9)):
        if flag.get("code") not in seen:
            seen.add(flag.get("code")); unique.append(flag)
    return unique
