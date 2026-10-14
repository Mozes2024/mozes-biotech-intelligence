# C1 API boundary — META-M1 / META-m09

Verified against production main `c0aa1af489efbf01ea79e194621530f3c04d90ec`.
The event GET route passed arbitrary `as_of` directly to `engine_v2.score_event`.
The radar route parsed a date but also scored current event records using it.

## Reproduction and availability boundary

An event request for 2021-01-15 returned HTTP 200 with old/revised market rows,
SCHEDULED/VERIFIED state updated in 2026 and a guidance window published in 2026
for January 2027. `score_event` reads current chronology/verified_window, event_state,
features, tradability and validation gates. A historical price filter does not
make those inputs historical. `prices` uses INSERT OR REPLACE and lacks as-of
revision/availability provenance. Current event state has no complete historical
version chain. Individual immutable source/price archives cannot establish a
complete archived scoring state for these routes.

The event route raised ValueError for malformed/impossible dates and timestamps.
Blank values silently became today; multiple dates used the first; Python's ISO
parser accepted compact/week dates outside the canonical API date format. Future
dates could produce fabricated future views or overflow later arithmetic.

## Minimal correction

Both current-state scoring GET routes validate one canonical YYYY-MM-DD before any
event lookup/scoring. Missing `as_of` defaults to the existing server-local today.
Malformed, blank, ambiguous and intraday values return JSON HTTP 400. Valid dates
other than today return JSON HTTP 422 `historical_scoring_unsupported`, including
the requested/supported date and the missing complete frozen provenance reason.
No current state, market score or recommendation is included in that response.

Default/explicit current-day scoring, unknown-event 404, live dashboard exports,
underlying live engine formulas, POST/paper behavior and investment gates are
unchanged. No new historical-state reconstruction or archival route is introduced.
Historical scoring can be added only with a separately verified complete immutable
as-of source/state/security/validation/price-revision record, not today's database.

## Regression-first verification

Before correction: 14 failures / 6 passes in `tests/test_c1_api_asof.py`.
The concrete mixed-state response, malformed date exceptions, blank/ambiguous
requests and unsupported formats reproduce the defects. After correction: all
20 tests pass, including later price revisions not changing the unsupported
response, no database lookup for rejected requests, unchanged default/current-day
results, valid radar and database preservation on historical refusal.

The independent PIT-cutoff PR addresses timestamp comparisons in frozen snapshots;
this branch is based on main and does not depend on it or B2/A5 branches. There are
no source/state/schema/ACK/receipt migrations, replay, monitoring triggers or
production actions. Existing frozen historical fixture analysis is not modified.
