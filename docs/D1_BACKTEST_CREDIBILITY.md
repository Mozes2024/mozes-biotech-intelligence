# D1 — descriptive returns versus executable strategy evidence

Baseline: `bb165fe130420a232a2d1436b5015a5e050b256d`.
Research/reporting correction only. No trading strategy, gate activation, migration,
historical rewrite, production scan or META-M8 staging implementation.

## Reproduced findings and disposition

The META-M3/META-M4-related concerns were checked against current code, rather
than assuming all earlier descriptions still apply.

| Boundary | Before | Correction / disposition |
| --- | --- | --- |
| v2 T-k offsets | `[-1-entry]` and `[-1-exit]` selected one observation too early and rejected exactly-entry observations | `[-entry]` and `[-exit]`, consistent with legacy math. T-1 is the last observed pre-announcement close. |
| Legacy benchmark | Independently indexed benchmark rows could represent different dates | Shared calculation requires identical stock/benchmark endpoint dates; missing benchmark becomes unavailable excess, not zero. |
| Missing announcement bar | Afterhours fell back to an earlier close; premarket/intraday could silently use a later date | Known-session event studies require the exact announcement-day close. Missing/holiday bars fail closed instead of inventing a session/fill. |
| Numeric historical bars | Zero/negative/nonfinite/malformed attached prices could divide by zero or create invalid returns | Validate copied series without writing them; invalid/duplicate stock series or overflow gives an unavailable window. Invalid benchmark gives unavailable excess. |
| Historical entry knowledge | Research-ready meant a snapshot before the final event, not necessarily before a T-30 entry | Separate read-only entry audit; require aware, blinded snapshots and original sources captured/published by that snapshot strictly before entry. No future snapshot or later-captured revision proves original entry knowledge. |
| Final catalyst date | All grids are retrospectively anchored to the final event date | Retain these as explicitly descriptive event studies. A typed pre-entry forecast differing from the final date is flagged; free-text schedules and complete schedule revision provenance are not inferred/certified. No executable result is claimed. |
| Legacy year walk-forward | December training events could be used for a January test position that entered in the previous October | Purge training events not completed before the earliest test entry across the fixed candidate grid. Require enough benchmark-matched training observations and never switch objectives opportunistically between raw and excess returns. |
| Active validation evaluator | Already uses a predeclared (30,7) window, descriptive chronological cohorts, `oos_n=0`, and preserves gate enabled state | This concern is already guarded; evaluator/gates are unchanged. No new OOS strategy or automatic gate opening is introduced. |
| Portfolio drawdown | Legacy code compounded potentially overlapping event returns as if sequentially funded | Keep the compatibility key `max_drawdown=null`; explicitly state why a portfolio equity curve cannot be reconstructed. The pure arithmetic helper is retained for existing callers. |
| Cost reporting | Gross historical moves only, with a generic cost warning | Preserve compatibility gross fields, add explicit gross aliases and separately reported assumed net distributions with scenario inputs. |

## Availability and provenance contract

`entry_audit` is diagnostic, not an execution engine. A date-only hypothetical
entry is conservatively bounded at UTC midnight; same-day premarket/afterhours
snapshots cannot establish a close fill. Sources retrieved after a snapshot do
not prove the version available at that snapshot, even if a publication date is old.

Historical batch assembly creates source-supported snapshots relative to the
final event (`historical_batch.build_bundle`). These remain useful descriptive
records, but their horizon names do not prove that the final event date was known
when an investor could have acted. Neither bundle generation nor stored histories
is changed. `readiness_for_case` retains its existing research-readiness contract;
it is not upgraded into execution certification.

Every currently reported executable strategy is explicitly unsupported, with
`n=0`, `oos_n=0` and `validated=false`. Case-specific exclusions are returned under
`executable_strategy.excluded_cases`, for a clearly labeled reference (30,7)
diagnostic, not a certification of every grid window. Complete schedule/source
binding and revision history, actual session/fill availability, prospective
registration, and provider/adjustment/vintage provenance are not established.

**META-M8 remains unresolved.** A provider name or frozen attachment alone cannot
certify adjustment basis or original capture provenance. No caller-provided flag
can unlock executable eligibility. The raw/adjusted split fixture therefore
remains descriptive and cannot become validated evidence.

## Session and execution limitations

Offsets count observed bars, not a verified exchange-session calendar. The
`session_basis` field says so explicitly. Missing internal sessions, holidays,
early closes, suspensions, stale bars and timezone precision cannot be repaired
from price dates alone. A missing known-session event-day bar is unavailable.
An afterhours close-to-next-close move can describe an overnight gap; it does
not assume that the pre-announcement close was executable after the news.

Unknown-session event moves remain timing-uncertain descriptions and are excluded
from empirical hold eligibility by existing readiness guards. There is no inferred
open/auction execution, bid/ask history, order volume, short/borrow availability,
liquidity model or intraday execution support in this change.

## Explicit cost sensitivity

`report(conn, cost_scenarios=...)` and `all_runup_grids(..., cost_scenarios=...)`
accept commission, half-spread and slippage basis points **per side**. Defaults
are uncalibrated illustrations, not estimates of biotechnology execution costs:

| Scenario | Commission | Half-spread | Slippage | Total per side |
| --- | --- | --- | --- | --- |
| frictionless reference | 0 | 0 | 0 | 0 bps |
| illustrative | 0 | 10 | 15 | 25 bps |
| stress illustration | 5 | 35 | 60 | 100 bps |

For gross return g and summed proportional per-side cost c:
`net_assumed = (1+g) * (1-c) / (1+c) - 1`.
This is a long-only proportional notional accounting convention. Entry costs
raise initial cash and exit costs reduce proceeds. It is not a historical fill.
For example, an illustrative gross +10% becomes approximately +9.451% at 25 bps
per side. Fixed-dollar fees, nonlinear market impact, overnight execution gaps,
low-liquidity capacity and borrow remain unmodeled. No missing return becomes zero.

The legacy `return`/`stats` fields remain gross. Explicit `gross_return`/
`gross_stats` and `net_assumed` separate the new reporting. Net statistics remain
descriptive assumptions; benchmark excess is gross and is not mislabeled net.

## Selection, uncertainty and tests

Every predefined v2 grid is still shown, not only a winner. Each reports considered
case count, excluded price windows, missing benchmark count and sample sizes.
Small-sample warnings and dispersion/range statistics do not establish independence
or a calibrated confidence interval; overlapping events and the convenience sample
remain limitations. Legacy purged folds report actual training cutoff/sample size,
test sample size, skipped folds, and cross-fold parameter selection counts. Changing
test return levels cannot select training parameters. Test data are not claimed
as genuine prospective OOS: no registered strategy exists.

Regression evidence: initial 20 cases failed on unchanged baseline; the first
corrected focused run passed. Two additional price/holiday boundaries were shown
failing before their correction. The explicit gross/net compatibility and
read-only report regression was also shown failing before its reporting patch.
Final test suite includes those adversarial cases plus changed catalyst forecasts,
late/missing timestamps, post-entry evidence, missing/duplicate sessions, unmatched
benchmarks, split/basis uncertainty, costs, cross-year training leakage, independent
test outcomes, small samples, frozen data and disabled gates.

Run `python -m pytest -q tests/test_d1_backtest_credibility.py`, full pytest and
repository CI. Only PR-triggered CI is authorized. No production experiment or
performance claim follows from passing tests.

## Final review — benchmark arithmetic boundary

The review at `468cff33afe709f4024743d553b96289a2a7a039` identified a remaining
gap: `abnormal_return` used raw series after `event_return` normalized a copy,
and `excess_return` did not independently validate its inputs. Zero denominators
crashed; nonfinite/negative prices and duplicate benchmark dates were not safely
rejected. Regression-first execution recorded 24 failing cases and one valid-data
control passing before correction.

Both calculations now validate copied stock and benchmark series, reject duplicate
dates, and check derived returns and model intermediates for numeric overflow.
`excess_return` returns `None` when unavailable. `abnormal_return` returns
`available=false`, a reason and null benchmark-relative/beta/CAR fields, while
preserving the independently valid `stock_return`. No invalid value becomes zero.
Valid relative-return, OLS beta and CAR formulas are unchanged.

Tests cover zero/negative/NaN/infinite/malformed values in estimation and event
windows, duplicate dates, numeric-string normalization, division/variance overflow,
and a real HOLD evaluator using an already-dirty **synthetic local** attachment.
The direct calculation preserves the entire DB; the evaluator retains frozen
sources/prices/snapshots/labels and existing gate settings/OOS counts while updating
its ordinary reporting metrics. No stored price is repaired, rewritten or replayed.
