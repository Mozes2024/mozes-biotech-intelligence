# v2E validation report

Base: d26e473f2a64346039f1bf83e49ac2ae7adf413e (main).

## Verified locally

- Full suite: **245 passed in 21.62s**, no skips/failures.
- Python 3.12.10: compileall for mozes, tests and scripts passed.
- Node v24.16.0: node --check passed for all 4 web JavaScript files.
- git diff --check passed.
- Workflow files changed: **none**. Standard push CI remains Python 3.11.
- No workflow was manually dispatched during implementation.
- No tracked pyc/__pycache__ artifacts. No production SLS-specific path.
- Fresh in-memory database + canonical local research import + evaluator:
  RUN-UP enabled=false, n=0, min_n=150; HOLD enabled=false, n=0, min_n=200.
  Descriptive RUN-UP n=30: P2=5, P3=7, PDUFA=18, ADCOM=0. HOLD eligible descriptive n=0.
- A pre-enabled test gate retains its enabled state and min_n; evaluator never enables gates.

## SELLAS acceptance

Fixture pipeline proves REGAL event-driven 78/80 as of 2026-05-11, no calendar date/days_to;
SLS009 Phase 2 Q4 2026 has a separate calendar window. Both coexist in public catalyst_chain
through generic mapping/extraction/promotion, without a production ticker special case.
The fixtures are bounded primary-source extracts with synthetic registry envelopes,
not a claim that today's live production catalog has been refreshed.

## Limitations

- Local tests used Python 3.12; Python 3.11 is left to existing push CI.
- No full live-source refresh or visual-browser audit was run. Source adapters and
  UI overlays were exercised with controlled fixtures. Deployment is not verified by local tests.
- Chronological reconstructed cases are descriptive, not prospective OOS. A registered
  strategy evaluation contract and calibrated probability model are not implemented.
- Discovery/coverage are bounded; shared-NCT/multi-study ambiguity remains review-only.
- Unknown publication times remain unknown. US exchange holidays/early closes and
  intraday-bar reaction attribution are not modeled.
- Hash verification detects payload/link tampering, not privileged whole-database
  replacement or complete chain truncation; retain external backups.
- Legacy paper entries remain unhashed and are counted explicitly. No runtime DB,
  validation enablement or checked-in static snapshot is replaced with fixture data.
- AI and workflows were left unchanged. Existing Node/runner lifecycle warnings were
  not addressed in a separate commit/run.

## Deployment sequence

Push one complete validated commit to main using existing authorization. Let existing
push CI/Pages automation run; do not manually dispatch duplicate runs. The next existing
deep refresh collects new coverage data. Back up any local runtime database before
upgrading; additive tables/triggers initialize on normal connection. Root v2D package
files remain historical, not the v2E release manifest.

## Exact changed files (38)

- `.gitignore`
- `README.md`
- `docs/METHODOLOGY.md`
- `docs/V2E_ACCEPTANCE.md`
- `docs/V2E_RELEASE_REPORT.md`
- `mozes/app_server.py`
- `mozes/backtest_v2.py`
- `mozes/coverage.py`
- `mozes/discovery.py`
- `mozes/engine_v2.py`
- `mozes/extract.py`
- `mozes/financial_context.py`
- `mozes/financial_intelligence.py`
- `mozes/financing_intelligence.py`
- `mozes/historical.py`
- `mozes/ingest/ctgov.py`
- `mozes/ingest/edgar.py`
- `mozes/ingest/nasdaq_trader.py`
- `mozes/intelligence_store.py`
- `mozes/live_intelligence.py`
- `mozes/live_monitor.py`
- `mozes/live_prices.py`
- `mozes/market.py`
- `mozes/paper.py`
- `mozes/payload_v3.py`
- `mozes/pipeline_v2c.py`
- `mozes/promotion.py`
- `mozes/refresh.py`
- `mozes/schema.sql`
- `mozes/session.py`
- `mozes/source_observability.py`
- `mozes/timing.py`
- `mozes/validation_evaluator.py`
- `tests/fixtures/sellas_v2e.json`
- `tests/test_market_v2.py`
- `tests/test_v2e.py`
- `web/coverage_ui.js`
- `web/index.html`
