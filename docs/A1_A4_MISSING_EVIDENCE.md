# A1+A4 — missing evidence versus observed negative evidence

Base main: `51a5535f66a5b016d487c69186bd36ab07acb7a3`. This branch is independent of Pages #29 and of the already merged clinical identity #21.

## Reproduced defects

The exact baseline assigns score 29 to an empty Phase-II feature set, which then becomes an avoid classification. An empty regulatory set receives a favorable base-rate score. Neither conclusion has event-specific observations. With `score=None` and an open hold gate, the baseline raises `TypeError: '>=' not supported between instances of 'NoneType' and 'int'`.

## Narrow correction

- Accepted observations retain explicit positive/negative booleans, known design/efficacy values and valid sample/class-failure counts. Missing/null/unknown values are not truthy safety or integrity risks. `prior='none'` means absent efficacy evidence and no longer creates a negative adjustment. Explicit `prior='weak'` and observed negative findings retain their adjustments.
- Clinical completeness requires efficacy, endpoint/population continuity, design, endpoint objectivity and sample size. Regulatory completeness requires efficacy and explicitly known CRL/advisory/FDA requirement/safety/integrity fields. These contracts deliberately favor insufficient information over a favorable transition prior.
- Empty observations have `score=None`; partial observations may retain an uncalibrated context score but cannot independently justify a positive or negative recommendation. Explicit negative evidence and critical source risks retain negative classification/recommendation even when other fields are missing.
- Missing clinical, regulatory, financial or numeric inputs produce `INSUFFICIENT_INFORMATION` where applicable. Finite known cash runway is required by the active engine for positive candidacy; missing runway produces an information gap, while an observed cash shortfall remains a negative risk. Unknown/nonfinite/bool scores never reach numerical gate comparisons. Missing gates stay closed, and only literal `satisfied=True` can admit an otherwise complete candidate.
- Existing terminal/quarantine/stale and informational-watch semantics retain precedence. Current clinical/classifier version becomes 2.1.0; old stored score records are untouched. The Hebrew UI renders the new classification/category as `חסר מידע`.

This does not calibrate a clinical probability, replace financial-statement provenance, or certify all features as source-verified. It conservatively consumes the existing source/PIT boundaries; separate B/C/D milestones remain open. Recommendations for incomplete existing events will become less actionable, intentionally.

## Verification and preservation

Frozen positive/negative/unknown fixtures and adversarial tests cover partial positive evidence, low priors without observed failure, explicit negative findings, null/nonfinite/unknown scores, empty evidence, open/closed/absent gates, missing versus short cash runway, malformed features, lifecycle precedence, immutable PIT snapshots and source-known dates.

A read-only active-engine test uses `PRAGMA query_only=ON`, checks total changes and compares the database SHA256 before/after. EDGE/CHG/CAT/ALT event rows, original source timestamps, old score rows/versions and locked validation gates remain unchanged. No migration, historical evaluation rewrite, backfill, identity edit, notification, receipt/ACK change or OOS gate activation is included.

The unknown-evidence test fails against exact baseline code; the baseline null-gate crash is independently reproduced. Focused engine/recommendation/PIT/historical suites pass. All three Worker JavaScript suites, JavaScript syntax and Python compile checks pass. The complete local Python run and exact-head Linux CI are reported in the PR; Docker and offline smoke belong to existing CI. No LLM provider calls or paid services are introduced.

`docs/BIO_IMPLEMENTATION_TRACKER.md` is the existing tracker from held #25, reconciled to current main and #29. R0a remains HOLD; R1/R2/A5/A3/B/C/D/E remain explicit pending work, and #17/#18 are not blindly merged.

## Rollback

Before approval, close the PR. After a separately approved merge, use a reviewed revert PR and normal CI. Restore code/version labels without modifying any historical evaluation, receipt, source, artifact, event identity or gate state. Reverting restores the old missing-evidence behavior; no data migration is required.
