# Prospective evidence capture — inactive, review-only increment

Production baseline: `828245bab4981dd66e0597d2d0d653af74f0f247` (PR47 merged).
No production migration/activation, source scan, historical backfill or notification.
No strategy or gate eligibility is granted by this capture mechanism.

## PR49 final disposition

PR49 HEAD `c7259308ffadeddbc0cb811fc823695287cd38ce` is unchanged, Draft and mergeable.
Exact-head CI https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38733094501
passed both jobs: 1,223 pytest, 104 frontend, Worker/DO/local D1/smoke, Docker build/imports.
There is no reason to repeat unchanged checks. The patch replaces duplicate run-up
benchmark arithmetic with the already reviewed `market.excess_return`. Three failures
and a valid-data control were demonstrated before correction; five added regressions
cover preservation and real reporting/evaluator behavior.

Its only production computation change after a future authorized merge is correct
unavailability of unusable benchmark-relative run-up returns. Derived descriptive
report/evaluator metrics may therefore change for those inputs. It does not write
prices, frozen attachments, source archives or histories, change alert eligibility,
open gates, or modify monitor/workflow ownership/configuration. Ordinary merge CI and
potential Pages publication are real consequences: GO for a separately authorized
controlled squash merge, not authorization to execute one here.

PR48 HEAD `90df292ed98ccf7721fe4073562813c5ccf60e8c` remains independent and inactive.
The all-frontend CI edit is identical in both PRs; product files do not overlap.
This capture PR also shares that identical CI coverage edit, but imports neither
unmerged branch. Its packaging test has no dependency on either implementation.

## Existing evidence inventory — reuse, do not rebuild

| Existing mechanism | What is already preserved | Remaining boundary |
| --- | --- | --- |
| `db.archive_source`, `source_archive` | Exact source content, SHA-256, URL, publication/retrieval timestamps; helper rejects changed identity/content/provenance | Timestamp assertions imported later are not proof of original historical availability; privileged SQL remains outside the helper's immutability guarantee |
| `trial_record_versions` | CT.gov fetched payload versions with retrieval and posted-update dates | Retrieval is not historical first availability; registry estimates are not issuer readout commitments |
| `change_events` | Append-only detected changes and source identities/hashes | Detection-oriented history is not a complete per-decision knowledge bundle or every forecast revision |
| `v2c_regulatory_versions` | Selected regulatory application guidance versions, previous/new window, source publication and observation | Does not cover every event/clinical forecast; does not certify a prospective trading rule |
| `clinical_events`, `clinical_event_history`, `clinical_catalysts` | Discovery/publication context and lifecycle transitions | History records lifecycle/reason/time, while current catalyst windows are updated; lifecycle history alone does not preserve all old forecast windows |
| `score_runs`, `VERSIONS` | Append-only score outputs with model versions, as-of and capture time | No complete source-version/gate/rule/forecast binding for a prospective decision |
| `historical_cases`, `feature_snapshots`, frozen prices, labels | Case-bound immutable-helper snapshots, attachments and outcomes | Retrospective assembly is not pre-entry knowledge; no rebuilding or relabeling here |
| `PaperBook`, `paper_audit`, `forward_candidates` | Immutable paper signals, hash chain/idempotency, fixed first qualifying research candidate | Paper signals and candidate metadata already enter API/Pages; observational records must not become signals or contaminate those semantics |
| Legacy shared `prices` | Current values/source label, case-bound frozen copies | META-M8 remains unresolved in production |
| PR48's inactive staging | Proposed implementation already preserves provider, field/basis, capture/vintage, matched selections and immutable manifests | Unmerged/inactive, not imported or reimplemented here; future rollout needs independent approval |

## Smallest isolated implementation

`mozes.prospective_capture` reuses the current DB connection, source archive reader,
model-validation reader and existing canonical evidence encoding/hash helpers. One
private additive table, `prospective_evidence`, stores complete observation envelopes.
It intentionally avoids reusing paper-signals or historical-case identities because
their existing consumers would misrepresent observations or alter frozen datasets.

`ensure_schema(conn)` is explicit, transactional DDL (one table/index and immutable
UPDATE/DELETE/replacement triggers). It is **not** called by `db.connect`, bootstrap,
monitor, API, export, CLI or module import. `record` refuses an uninitialized schema.
Both initializer and writes require exact `MOZES_PROSPECTIVE_CAPTURE=1`; absent/other
values fail before DB access. No repository variable/configuration is set by this PR.

Each envelope has deterministic SHA-256 capture identity from stable event ID and
caller observation key, request hash, payload hash, server-clock capture UTC and a
previous-capture link. It preserves event/ticker/type, forecast window and declared
issuer/inferred/unknown origin, original forecast metadata timestamps (unknown stays
null), declared announcement session, full analysis/feature output and model versions,
code SHA reference, source IDs/URLs/content hashes/publication/retrieval timestamps,
selection-rule ID/definition, actual gate rows and explicit exclusions.

Capture UTC is generated internally, never supplied by an importer. New observations
are timestamped after their source/gate inputs have been materialized, so a slow read
cannot make an observation appear available before it was captured. A regression was
shown failing before this boundary was corrected.
New observations
require a current-UTC-day analysis or an aware analysis within the prior five minutes.
No historical backdating is accepted. Known future source/feature knowledge clocks,
future last-close dates and structured outcome fields are rejected. Future forecast
windows are permitted because a prediction is not a future observation. Date-only
analysis time remains insufficient for an intraday decision or close fill.
When analysis supplies an aware decision instant, source publication/retrieval,
forecast metadata, feature knowledge clocks and gate-update times must not exceed
that instant, even if capture happens a few minutes later. The envelope records both
`decision_evidence_cutoff` and actual capture time. Four adversarial lag-window tests
failed before this guard and pass after it; capture lag cannot justify borrowing later
information or reconstructing a gate state overwritten after the decision.

`availability.observed_by_bio_at` describes this capture; historical first availability
is always explicitly unknown. Source publication/retrieval and caller-declared forecast
created/discovered/modified/available times remain separate. Caller declarations do
not become certified historical facts. Observation capture is not an assertion that
all sources were correctly bound to each feature: that remains an exclusion.

Identical retries return the original capture, including after a later day or gate
metadata update; they never recapture current state under an old identity. Changed
caller input under one key raises `Conflict`. New keys preserve new observations,
including A→B→A forecast revisions. Caller keys must represent the actual observation
slot/run, not just forecast content, or returning to A could be mistaken for a retry.
Same-event clock rollback refuses a new append. SQLite transactions serialize the
read-state/append boundary; busy/disk/validation errors propagate without partial writes
or falsely reporting success. Outer caller transactions are not committed.

`as_of(conn,event_id,aware_timestamp)` returns stored, hash-checked observations whose
capture timestamp is at/before that instant. It never rescores from current state,
joins later forecasts/outcomes/prices, reconstructs earlier knowledge or silently hides
a corrupt row. Reads remain available after capture is disabled. SQL triggers protect
ordinary DML, not privileged schema changes or clock/host compromise. Capture timestamp
accuracy ultimately depends on the authorized host clock and caller/source integrity.

## Price and execution boundaries

There are **no** price tables, adapter calls, selected-series values, return calculations
or duplicate M8 storage here. Raw price-series arrays in analysis are rejected. Current
model output/market features may be recorded as unverified analysis, not certified
prices. A price reference is either unknown or an explicitly **unverified** PR48-style
`mozes-price-staging-manifest-v1` attachment ID plus SHA-256. Changed vintage/hash under
the same observation key conflicts. It grants no eligibility and is not resolved by
importing an unmerged module. Future integration must verify the referenced immutable
manifest, independently trace stock/XBI providers/bases/vintages and enforce matched
sessions through the one M8 selection path; no return calculation may bypass it.

All captures are non-authoritative, `eligible=false`, `executable=false`. Actual fills
are null, with exclusions for unverified execution/session data, schedule-source
binding, price provenance and unregistered strategy. Existing gates are read only;
input classification cannot enable RUNUP/HOLD or promote a record. No notification,
outbox, candidate, paper signal, public export or monitoring hook is added.

Executable validation separately requires an independently verified exchange/session
calendar (including timezone/early closes/halts), actual decision/order/fill clocks,
bid/ask and obtainable size/volume, explicit entry/exit convention, observed versus
modeled spread/slippage, gaps, liquidity/capacity and borrow/fees where applicable.
Missing facts remain unavailable. No attractive retrospective result substitutes for
these observations, and no investment alpha/OOS claim follows from these captures.

## Budgets, failures and retention

Hard limits: 64 source references, 256 KiB/envelope, 10,000 captures and 64 MiB total
UTF-8 payload bytes. Inputs and final envelope are bounded; no truncation or deletion.
The first reached limit aborts a new append. Records and readable history remain.
These are logical budgets, not a filesystem quota; separately provisioned DB/checkpoint
capacity and measured growth are required before rollout. No retention job or cron.
For illustration only: 40 captures/day reaches 10,000 records in 250 days, but the
byte limit can be reached earlier depending on real envelope size. Capture every hot
tick is not authorized. A future reviewed caller should use a bounded event allowlist,
source/forecast revisions and a preregistered decision cadence within existing ownership.
On budget/busy/invalid-source failure the caller must report capture incomplete; it
must not change alert/monitor success semantics or silently drop evidence.

## Activation and rollback — not performed

1. Approve this inactive code separately from PR49's controlled merge. No dependency
   on merging/activating PR48 or A5. Recheck CI/production state before any merge.
2. Separately authorize the additive table initializer on the authoritative monitor
   DB after verified backup, schema/restore dry run, size/locking assessment, storage
   quota and checkpoint/manifest compatibility. No old row is migrated or backfilled.
3. Review a bounded capture caller in the existing serialized Python monitor lineage;
   define stable run/slot keys, failure reporting, source binding and decision rules.
   No hot Cloudflare path, second writer, new schedule or activation is added here.
4. Under explicit rollout approval, enable the capture flag and observe one ordinary
   scheduled run: reconcile source hashes/times, forecast revisions, exact rule/gate
   snapshots, persistence/restoration and unchanged publication/alert histories.
5. Roll back by disabling capture/removing the new caller through reviewed code;
   retain the table and every record/checkpoint. Old code ignores the extra table.
   Do not drop storage, rewind checkpoints, rewrite forecasts or replay notifications.

GO for review of the inactive increment if exact-head CI passes. HOLD for production
DDL/caller/activation, execution eligibility and OOS/alpha claims. PR35 HOLD; PR46 excluded.

## Prioritized research roadmap

1. Begin audited forward source/forecast/decision capture with bounded, preregistered
   inclusion/exclusion rules; independently verify literal forecast/source bindings.
2. Complete separately approved M8 writer/selector rollout; bind verified immutable
   stock/XBI manifests to forward observations without relabeling old attachments.
3. Obtain verified session calendars and measured execution evidence; freeze strategy,
   cost assumptions and allocation/overlap rules before seeing evaluation outcomes.
4. Accumulate blinded, chronologically sealed prospective cohorts and separately
   versioned outcomes; test leakage, missingness, sample dependence and uncertainty.
5. Only after adequate preregistered evaluation, assess genuine OOS performance and
   a funded portfolio equity curve. Any investment-gate decision remains separate.
