"""Catalyst lifecycle state machine.

Events are not permanent calendar rows. They can resolve early, be delayed, cancelled,
quarantined, or superseded. This module prevents stale PDUFA/readout rows from remaining
"upcoming" after an outcome is known.
"""
from __future__ import annotations

UPCOMING = {"CANDIDATE", "DISCOVERED", "VERIFIED", "SCHEDULED"}
RESOLVED = {"RESOLVED_SUCCESS", "RESOLVED_FAIL", "RESOLVED_MIXED", "APPROVED", "CRL", "WITHDRAWN"}
TERMINAL = RESOLVED | {"CANCELLED", "SUPERSEDED"}

ALLOWED = {
    "CANDIDATE": {"DISCOVERED", "VERIFIED", "QUARANTINED", "CANCELLED", "SUPERSEDED"},
    "DISCOVERED": {"VERIFIED", "SCHEDULED", "QUARANTINED", "CANCELLED", "SUPERSEDED"},
    "VERIFIED": {"SCHEDULED", "DELAYED", "QUARANTINED"} | RESOLVED | {"CANCELLED", "SUPERSEDED"},
    "SCHEDULED": {"DELAYED", "QUARANTINED"} | RESOLVED | {"CANCELLED", "SUPERSEDED"},
    "DELAYED": {"VERIFIED", "SCHEDULED", "QUARANTINED"} | RESOLVED | {"CANCELLED", "SUPERSEDED"},
    "QUARANTINED": {"DISCOVERED", "VERIFIED", "CANCELLED", "SUPERSEDED"},
}


class InvalidTransition(ValueError):
    pass


def can_transition(old: str, new: str) -> bool:
    if old == new:
        return True
    if old in TERMINAL:
        return False
    return new in ALLOWED.get(old, set())


def transition(old: str, new: str) -> str:
    if not can_transition(old, new):
        raise InvalidTransition(f"invalid catalyst transition: {old} -> {new}")
    return new


def is_actionable(status: str) -> bool:
    return status in {"VERIFIED", "SCHEDULED", "DELAYED"}


def is_resolved(status: str) -> bool:
    return status in TERMINAL
