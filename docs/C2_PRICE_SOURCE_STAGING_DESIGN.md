# C2 / META-M8: price source collision — design for approval only

Production baseline: `c0aa1af489efbf01ea79e194621530f3c04d90ec`.
No schema, adapter, selection, live-scoring or historical-price behavior is changed
by this PR. All diagnostic writes occur in synthetic SQLite `:memory:` databases.

## Confirmed boundaries

`prices` has PRIMARY KEY(ticker,date). Both `db.store_prices` and
`ingest.prices.store` use INSERT OR REPLACE and replace close, volume and source
without comparing provider, adjustment convention or vintage. The prior observation
and provenance disappear from that shared table. A source label is not source isolation.

Run `python -m scripts.audit_price_source_collision` offline. For both writers,
two CSV raw closes at 100 become a mixed series after one Tiingo-adjusted close at
50 replaces the same ticker/day. The apparent return is -50% in a synthetic 2:1
split example with no modeled economic decline. The source label is replaced too.
This is not evidence of a real production split or measured historical contamination.

| Observed adapter path | Chosen values | Missing provenance |
| --- | --- | --- |
| Yahoo chart | adjclose when present, otherwise quote.close, per row; quote.volume | No persisted per-row basis, adjustment policy/version, captured time or response hash; raw/adjusted fallback can mix |
| Tiingo | adjClose and adjVolume | No persisted adjustment definition, response vintage/hash or per-row capture time |
| CSV | adj_close when nonempty, otherwise close; supplied volume | No declaration of raw/split/dividend adjustment, currency, volume basis or data vintage |

These are code observations, not independently verified claims about a provider's
current corporate-action methodology. The exact adjustment policy must be supplied
and documented before staging selection is authorized. No live feed was requested.

`historical.backfill_prices` currently stages ticker and XBI through the shared
table, records import-run metadata and then attaches by source. CSV run metadata
retains input hashes/names; Yahoo retains adapter/range. The daily table does not
retain run ID or observation capture time. Before attachment, another provider can
overwrite rows and leave gaps in source-filtered coverage. Same-provider revisions
can also overwrite earlier vintages without an adjustment-basis check.

`db.attach_historical_prices` copies values into case-bound historical_case_prices;
existing attachment identity cannot be changed through that function. Later shared
price replacement does not modify those already copied rows (verified by the probe).
This is application-level frozen attachment behavior, not proof against privileged
direct SQL writes. Run requested_at/attached_at are import/capture metadata, not the
market price's actual first availability time. Existing frozen values must never
be regenerated from today's staging rows or relabeled with invented metadata.

## Proposed non-destructive staging boundary

Approval is required before implementing this design or any schema migration.

1. Stage append-only observations separate from `prices` and frozen attachments.
   Each import has an immutable run ID, provider/adapter version, retrieval UTC,
   requested range, raw response/file SHA-256, provider adjustment documentation,
   currency, exchange/timezone/session convention and bar frequency. Retain both
   raw and adjusted close/volume when supplied; missing values remain unknown.
2. Observation identity includes issuer/security identity, session date, provider,
   declared price/volume adjustment basis, currency and run/vintage ID. Keep later
   revised observations beside earlier ones. Same-run same-hash retry is idempotent;
   a conflicting payload under one identity is quarantined, not replaced.
3. Selection is explicit and records the chosen run/series and its rationale.
   Require one compatible basis/vintage across the entire requested stock window;
   benchmark selection must document its own compatible basis and matched session
   dates. Do not splice raw into adjusted rows or infer a -50% return across bases.
   Unknown CSV basis and provider adjustment ambiguity stay unselected for research.
4. Keep first-availability time distinct from observation retrieval and import time.
   Preserve provider revision/corporate-action vintage; current adjusted history is
   not proof that the same numeric close was available at a past cutoff. No new
   historical scoring eligibility is granted by provider labels alone.
5. Existing shared prices remain unchanged until a separately approved selector
   rollout. Legacy observations with unknown basis/capture are marked unknown in
   a separate record; do not backfill timestamps, infer provenance or rewrite them.
   Existing case-bound snapshots, IDs, price attachments, sources, ACKs and receipts
   remain fixed. New attachments copy from a reviewed staging selection with an
   immutable manifest and matched benchmark coverage; old attachments are never replayed.

## Separate implementation gate

Before authorization: specify storage shape/budgets, provider/basis taxonomy,
append-only enforcement, selection permissions, compatibility with existing price
consumers, revision/split/dividend tests, atomic stock/benchmark capture, and rollback
without historical rewriting. Offline fixtures must cover multiple providers, raw
versus adjusted closes, two split-adjusted vintages, same-run conflicts, unknown
CSV basis, missing benchmark days and unchanged frozen attachments.

GO for review of this design/evidence; HOLD for schema, staging activation, provider
reconciliation, reprocessing or historical replay. The separate numeric-safety PR
rejects malformed rows and calculations but does not solve valid-provider collisions.
