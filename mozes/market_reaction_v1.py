"""Uncalibrated market-reaction context, separate from clinical evidence scoring."""
from __future__ import annotations


def features(context: dict) -> dict:
    """Return available deterministic inputs; missing data stays missing."""
    names = ("market_cap", "pre_event_runup_20d", "liquidity", "volume",
             "cash_runway_months", "active_atm", "active_shelf", "s3_filing",
             "filing_424b5", "recent_financing")
    return {name: context.get(name) for name in names}
