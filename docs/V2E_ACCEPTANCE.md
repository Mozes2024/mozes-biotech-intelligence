# v2E acceptance evidence

## SELLAS regression (not a current market assertion)

`tests/fixtures/sellas_v2e.json` holds bounded, attributed primary-source extracts and
official SEC/Nasdaq identity fields. Registry envelopes are explicitly synthetic test
inputs, not a claimed archive of actual registry responses. No fixture is loaded by
production. The issuer first-quarter release supplies REGAL's 80-event target and
78-event observation as of May 11, 2026; the second-quarter release supplies SLS009
first-line Phase 2 Q4 2026 guidance. Source URLs are retained in the fixture.

Generic extraction, official entity mapping, primary promotion, scoring and public
payload tests prove:

- REGAL is EVENT_DRIVEN, 78/80, calendar window and days_to null.
- SLS009 is a distinct calendar event, October 1–December 31, 2026.
- Both coexist in each event's catalyst_chain; repeated promotion does not duplicate
  change notices. A source scan checks no SLS-specific production path exists.
- Fresh-database RUN-UP and HOLD enabled flags remain false. A deliberately pre-enabled
  test gate retains its prior value and threshold after evaluation.

## Scope and limitations

No new schedule, dependency or workflow file is needed. Existing local tests cover
the new timing, provenance, queue, ledger, discovery, coverage, session and failure
contracts alongside v2C/v2D regressions. Browser-overlay tests use Node with a fixture
DOM; they are not a full visual-browser audit. Network adapters are mocked in regression
tests, so fixture acceptance does not prove today's live source availability/catalog.

Discovery/promotion remain bounded and conservative, not exhaustive. Separate programs
sharing one NCT and ambiguous statements require review; the auto-event key stays
ticker/NCT for compatibility. Automatic extraction does not resolve every issuer's
publication-time markup. Unknown times remain unknown, and SEC acceptance is not used
as an event clock. Full prospective strategy validation, confidence intervals, exchange
early-close calendars and externally anchored ledger integrity are not implemented.

## Deployment

Apply this release as one complete commit. Existing SQLite databases acquire additive
tables/triggers through the normal connection schema initialization. Back up the runtime
database first. Do not replace it with a test/fixture database. No gate enablement
migration is performed. Let the existing deep refresh populate new evidence/coverage;
do not manually dispatch workflows just to force these data. No checked-in static
snapshot is regenerated from test fixtures.

The root START_HERE_HE.md, MANIFEST.json and VALIDATION_REPORT.md describe the earlier
v2D manual-upload package; v2E release evidence is this document and its release report.
