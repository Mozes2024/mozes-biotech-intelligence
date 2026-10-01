# Methodology (v2D)

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
commercial stage, pivotal designation, filing enablement, and short cash runway. Risk
flags remain independent from evidence and can force conservative classifications.

## Validation gates

RUN-UP and HOLD-through are locked by default in `model_validation`. A gate is satisfied
only when it is explicitly enabled and the stored observed OOS count meets its existing
minimum (`150` for RUN-UP, `200` for HOLD). `mozes validation-evaluate` calculates a
chronological walk-forward report over clean, case-attached prices and writes counts,
fixed-window/fold metrics, and notes to `model_validation`.

The evaluator never changes `enabled`, never selects the best grid, and never opens a
gate. Historical seed cases marked `legacy_post_hoc`, unblinded snapshots, unverified
labels, missing provenance, missing case-attached prices, and unknown announcement
sessions remain excluded by the existing readiness/PIT checks.

## Operational evidence

Refresh metadata persists source-operation counts, errors, duration, and SEC cache
hits/misses where available in `refresh_runs.details_json`. These are operational
observations, not model features. The local server binds to `127.0.0.1` by default;
refresh requests run in a background job and return a run identifier for polling.
