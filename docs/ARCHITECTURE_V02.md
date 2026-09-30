# v0.2 Architecture Decisions

## Non-negotiable invariants

1. Discovery is not verification.
2. Outcomes never enter a pre-event feature snapshot.
3. Primary-source date evidence is required for actionable scheduling.
4. Event lifecycle is mutable through appendable evidence; historical paper signals are immutable.
5. PDUFA/AdCom and first pivotal readouts use different evidence engines.
6. No trading-classification gate is unlocked by hand.
7. Benchmark data is joined by trading date.
8. Event returns account for announcement session.
9. Post-hoc historical annotations are ineligible for validation.
10. Missing data lowers confidence; it is not silently imputed as favorable.

## Event states

```text
CANDIDATE -> DISCOVERED -> VERIFIED -> SCHEDULED
                    |          |          |
                    v          v          v
              QUARANTINED   DELAYED   RESOLVED / APPROVED / CRL
```

Terminal states cannot be reopened without creating a superseding event.

## Verification

ClinicalTrials.gov creates candidate records. It cannot by itself produce a verified readout date. Primary SEC/FDA/company sources can promote candidates.

Secondary-only dates enter quarantine.

## Backtesting

The backtester reports the full run-up grid. A best-looking in-sample window does not become a strategy. Release gates require out-of-sample coverage and stability.
