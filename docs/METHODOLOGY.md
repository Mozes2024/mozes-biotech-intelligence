# Methodology (v2E)

## Scope and safety boundary

MOZES is a point-in-time research system, not a trading system. ClinicalTrials.gov is
discovery only: its completion dates are sponsor estimates and cannot verify a catalyst.
Only primary-source evidence can promote an event. Scores are documented heuristics and
are not calibrated probabilities, recommendations, or gate overrides.

## Single active scoring path

`mozes.engine_v2.score_event` is the sole supported scoring path for the API, product
payload, paper ledger, radar, and CLI. It uses shared deterministic impact and risk
components from `mozes.scoring_common`; their explanation identifiers are stable English
codes. The Hebrew web UI translates those codes and user-facing status text.

`mozes.scoring` and `mozes.analysis` are v0.1 compatibility modules retained only for
frozen regression fixtures. They are not product paths. Old command names `export-v2`
and `serve-v2` are compatibility aliases that route to the v3 product surface.

## Evidence engines

Readout and regulatory events use separate evidence engines. The 2011–2020 BIO/Informa
transition context priors are starting context only, not event probabilities. Readout
evidence considers prior efficacy, endpoint/population continuity, regimen, design,
endpoint objectivity, mechanism, class failures, safety, integrity and sample size.
Regulatory evidence separately considers the clinical package, prior CRL, AdCom, FDA
requirements, safety and integrity. Categories are Weak (<45), Moderate (45–64), Strong
(65–79), and Very Strong (80+).

Impact is additive and capped at 100: event type, program dependency, market-cap band,
commercial stage, pivotal designation, and filing enablement. Short cash runway is
risk/dilution context only and never adds positive catalyst impact. A separate
`market_reaction_v1` feature contract may later describe market response; it is
not a clinical probability model. Risk flags remain independent from evidence and
can force conservative classifications.

## Validation gates

RUN-UP and HOLD-through are locked by default in `model_validation`. A gate is satisfied
only when it is explicitly enabled and the stored observed OOS count meets its existing
minimum (`150` for RUN-UP, `200` for HOLD). `mozes validation-evaluate` calculates a
chronological descriptive fold report over clean, case-attached prices and writes
descriptive counts, fixed-window/fold metrics, and notes to `model_validation`.
Reconstructed historical snapshots alone cannot establish prospective OOS performance.
The current evaluator therefore reports `oos_n=0`, `eligible=false`, and the explicit
limitation `prospectively_registered_strategy_required`; it does not manufacture OOS
from chronological sorting. Actual registered prospective-strategy evaluation remains
future work. Existing `enabled` and minimum-count values are preserved exactly.

The evaluator never changes `enabled`, never selects the best grid, and never opens a
gate. Historical seed cases marked `legacy_post_hoc`, unblinded snapshots, unverified
labels, missing provenance, missing case-attached prices, and unknown announcement
sessions remain excluded by the existing readiness/PIT checks.

P2_TOPLINE, P3_TOPLINE, PDUFA and ADCOM are reported separately (unknown types in
OTHER), never pooled into a headline edge. The daily market model estimates alpha/beta
from up to 120 matched daily returns strictly before the event window, requiring 30
observations and nonzero benchmark variance. CAR sums daily residuals; missing history
leaves beta/CAR null while retaining available simple XBI-relative comparisons. These
are descriptive estimates without calibrated probability claims or inferential CIs.

## Event timing and provenance

Deterministic extraction recognizes event/death counts, enrollment completion, database
lock, DSMB/IDMC review, and final-analysis milestones. `EVENT_DRIVEN` has null calendar
window and null `days_to`; even `TRIGGER_REACHED` is only an observed count, not an
automatic clinical outcome or lifecycle resolution. Latest confirmed progress retains
its own as-of/source/quote if a newer statement merely repeats the target.

Promotion requires primary-source evidence and a mapped study/program. Multiple study
matches remain unpromoted. CT.gov completion dates remain discovery only. Discovery
looks back 730 days (configurable cap 1095); completed studies must have an update in
the last 180 days. A Coverage Audit examines at most 1,000 candidates and 1,000 stored
primary statements and exposes truncation; it does not verify or score findings.

Issuer/wire `primary_publication_at`, when explicitly supplied in an archived historical
bundle, determines event session ahead of a supplied event timestamp. SEC acceptance is
fallback metadata, not true announcement time. Unknown sessions are excluded. After-hours
returns use event-day close to next trading close; premarket/intraday use prior close.
Pre-event features exclude event-day close except for after-hours announcements. Daily
bars cannot isolate intraday reactions. Calendar/session helper supports modern US DST
rules; exchange holidays/early closes are not modeled and need explicit reviewed inputs.

## Forward ledger

Paper signals and outcomes retain their append-only/write-once contracts. New signals
from the local API must use today's snapshot; current inputs cannot be backdated.
New signals
include source and input hashes, versions, as-of/recorded-at and previous/current digest.
The verifier checks persisted payload and audit links. Old signals remain visibly
`legacy_unhashed_count` rather than being backfilled with invented audit history.
SQLite triggers prevent ordinary edits/deletes, not privileged replacement of the entire
database: externally anchored backups are needed to detect full-chain rewrite/truncation.
The fixed research candidate queue stores first qualifying snapshots without modifying
signals or gates. It is prospectively collected research, not validated trading evidence.

## Operational evidence

Refresh metadata persists source-operation counts, errors, duration, and SEC cache
hits/misses where available in `refresh_runs.details_json`. These are operational
observations, not model features. The local server binds to `127.0.0.1` by default;
refresh requests run in a background job and return a run identifier for polling.

Metrics include successes, last success/error, per-source partial/failure state and
cache counters where implemented. Refresh, monitor and existing operation metadata
persist them; price failures remain best-effort and visible in health. Coverage is
bounded, not a guarantee of exhaustive catalyst discovery. Pages is read-only; mutation
controls require the local API. English internal codes are translated in the Hebrew UI.
