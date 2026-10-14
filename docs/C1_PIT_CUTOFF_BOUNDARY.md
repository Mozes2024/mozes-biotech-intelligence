# C1 snapshot cutoff boundary — META-m12

Verified against production main `c0aa1af489efbf01ea79e194621530f3c04d90ec`.
`pit.snapshot_at` compared a date-only price string directly with an intraday
cutoff. Thus `2021-01-15 < 2021-01-15T09:00:00-05:00` included a same-day closing
price before the close. `assert_point_in_time` accepted the same false comparison.
Publication timestamps were truncated by `pub_effective`, discarding offsets;
2021-01-14 23:30 -05:00 was incorrectly available before 2021-01-15 00:00 UTC.

## Supported availability boundaries

- Frozen snapshots accept one canonical date-only YYYY-MM-DD cutoff. Prices and
  benchmark daily rows are strictly before that calendar date, preserving T-1.
  Missing/malformed/non-date-only price row dates are unavailable, not string-sorted
  evidence. No current/revised live database is read or reconstructed here.
- Year/month source dates retain `pub_effective`'s conservative period-end rule;
  date-only publication days strictly before the cutoff remain available.
- For source records that carry a precise aware publication timestamp, compare the
  actual UTC instant against cutoff midnight UTC. Naive clocks, invalid dates and
  absent publication time fail closed. This is publication availability, not an
  inference that an event outcome occurred at publication time.
- Intraday snapshot cutoffs, including after-hours and timezone-aware cutoffs,
  explicitly raise `intraday_snapshot_unsupported`. Current daily price inputs have
  no actual close/publication/retrieval timestamp, revision vintage or verified
  exchange session calendar. Assuming 16:00 or a lexical date boundary would not
  account for early closes, holidays or delayed/revised price publications.
  No intraday snapshot is produced until complete provenance/session support is
  separately implemented. This is a fail-closed unsupported boundary, not a claim
  that session-aware historical scoring is now supported.

## Minimal diff and regressions

Only `pit.py` changes: cutoff validation, validated UTC source availability,
validated daily price comparisons, chronological version ordering and defense-in-
depth revalidation. The global PIT availability helper in `dates.py` is unchanged.
Deep freezing, event IDs, input histories and valid date-only fixture semantics
remain intact. Live `engine_v2` scoring/monitoring/exports are untouched.

Before correction, the new suite had 17 failures / 4 passes: pre-close/after-hours
cutoffs admitted unproven closes, invalid cutoffs gave misleading snapshots, an
injected cutoff passed defense-in-depth, malformed price dates crashed/compared
lexically, and source clocks lost timezone/availability. After correction, all
21 new cases pass; together with leakage/backtest regressions, 41 tests pass.
Tests include missing publication time, later revisions, timezone-crossing release,
immutable price data, prior-day semantics and post-construction tampering.

This is independent of the current-state API PR and B2/A5 PRs. No market calendar
dependency, price schema, stored snapshot, historical replay, receipt, notification,
investment gate or production configuration changes are included.
