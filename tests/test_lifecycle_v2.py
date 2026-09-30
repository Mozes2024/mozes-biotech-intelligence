import pytest

from mozes.lifecycle import InvalidTransition, can_transition, is_actionable, is_resolved, transition


def test_lifecycle_allows_verified_to_approved():
    assert transition("VERIFIED", "APPROVED") == "APPROVED"
    assert is_resolved("APPROVED")


def test_terminal_event_cannot_be_reopened():
    assert not can_transition("APPROVED", "SCHEDULED")
    with pytest.raises(InvalidTransition):
        transition("CRL", "VERIFIED")


def test_quarantine_is_not_actionable():
    assert not is_actionable("QUARANTINED")
    assert is_actionable("SCHEDULED")
