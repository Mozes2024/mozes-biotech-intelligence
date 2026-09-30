"""Empirical release gates for trading classifications.

No HOLD-through or RUN-UP recommendation is enabled merely because code exists. Gates
must be unlocked by out-of-sample evidence and minimum sample sizes.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationGate:
    name: str
    enabled: bool
    min_oos_n: int
    observed_oos_n: int
    reason: str

    @property
    def satisfied(self) -> bool:
        return self.enabled and self.observed_oos_n >= self.min_oos_n


DEFAULT_RUNUP_GATE = ValidationGate(
    name="runup", enabled=False, min_oos_n=150, observed_oos_n=0,
    reason="requires positive out-of-sample median excess return with stable walk-forward folds",
)
DEFAULT_HOLD_GATE = ValidationGate(
    name="hold_through", enabled=False, min_oos_n=200, observed_oos_n=0,
    reason="requires calibrated probability model and positive out-of-sample risk-adjusted utility",
)


def gate_summary(runup=DEFAULT_RUNUP_GATE, hold=DEFAULT_HOLD_GATE) -> dict:
    return {
        "runup": {"enabled": runup.enabled, "satisfied": runup.satisfied, "n": runup.observed_oos_n, "min_n": runup.min_oos_n, "reason": runup.reason},
        "hold_through": {"enabled": hold.enabled, "satisfied": hold.satisfied, "n": hold.observed_oos_n, "min_n": hold.min_oos_n, "reason": hold.reason},
    }
