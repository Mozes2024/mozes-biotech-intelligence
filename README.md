# MOZES Biotech Catalyst Intelligence v0.3

A skeptical, point-in-time research system for discovering, verifying, scoring and forward-testing biotech catalysts.

The project is designed around one rule: **an upcoming catalyst is not a bullish signal**. The system separates event importance, clinical/regulatory evidence, market setup, provenance and lifecycle state. It does not unlock RUN-UP or HOLD-through classifications until an out-of-sample validation gate is satisfied.

The user interface is Hebrew RTL. Code and engineering documentation are English.

## v0.3 product layer

v0.3 adds the daily-use product surface on top of the v0.2 research engine:

- `mozes app` runs a local UI + JSON API on `127.0.0.1`.
- The dashboard includes a filterable Radar, event detail drawer, provenance, risks, evidence explanations, CT.gov candidates, resolved events, Paper Signals, validation and system status.
- `mozes export-v3` creates `web/data.json` for static/cloud hosting.
- The SPA works in both modes: live API when `mozes app` is running, or a read-only static snapshot on GitHub Pages.
- GitHub Pages deployment is included in `.github/workflows/pages.yml`.

### Recommended start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
mozes bootstrap-v2
mozes app --port 8000
# open http://127.0.0.1:8000
```

For a static snapshot:

```bash
mozes export-v3
```

See `docs/PRODUCT_V03.md` for the product/API surface.

## What changed in v0.2

v0.2 combines the strongest ideas from two independent prototypes and fixes the most important failure modes found in review:

- Database-driven live radar instead of scoring static JSON only.
- ClinicalTrials.gov is **discovery-only**. Primary-completion dates are sponsor estimates, not assumed readout dates.
- Primary-source verification tiers: SEC / FDA / company IR can promote an event; secondary calendars are quarantined.
- First-class event lifecycle: candidate, discovered, verified, scheduled, delayed, resolved, approved, CRL, cancelled, superseded, quarantined.
- Early resolution removes stale calendar rows. The seed correction for PHAR demonstrates this behavior.
- Separate evidence engines for clinical readouts and regulatory decisions.
- Both RUN-UP and HOLD-through are empirically gated and locked by default.
- SEC EX-99 exhibit crawling is supported for catalyst extraction.
- SEC-first regulatory discovery can create PDUFA/AdCom events without a CT.gov candidate.
- Announcement-session handling: premarket / intraday / after-hours / unknown.
- Calendar-aligned benchmark returns; no row-position alignment.
- Historical quality audit prevents post-hoc/unclean seed data from unlocking model gates.
- Hebrew RTL v0.2 dashboard reads the database-driven payload.

## Current status

This is a strong research foundation, **not yet a validated trading system**.

The included historical seed remains intentionally ineligible for model validation because it was reconstructed post hoc and lacks fully verified price/timestamp coverage. v0.2 will not treat that seed as evidence of economic edge.

## Historical Intelligence Dataset backfill

`historical-import` accepts a versioned JSON bundle containing immutable source archives, clean cases, frozen pre-event feature snapshots, and separately provenanced outcome labels. A source must have a canonical URL, source type, publication/retrieval timestamps, and archived content (hashed at import). A snapshot is rejected if either it or a cited source postdates the event cutoff. ClinicalTrials.gov may be used for discovery/structure only; its primary-completion date is never a readout date.

```bash
# archive source content supplied in the bundle, then import safely (repeatable)
mozes historical-import --file path/to/bundle.json
# or fetch source bodies explicitly when the bundle intentionally omits them
mozes historical-import --file path/to/bundle.json --fetch-sources

# attach T-120..T+30-ish (calendar-bounded) ticker and XBI history with provenance
mozes historical-prices --case CASE_ID --provider csv --stock-file ticker.csv --benchmark-file xbi.csv
# keyless best-effort alternative; no paid key is required
mozes historical-prices --case CASE_ID --provider yahoo

mozes historical-readiness
mozes historical-export --file historical-status.json
```

`research-ready` requires a non-legacy blinded snapshot, archived source provenance, a verified outcome, a known announcement session, and ticker/XBI price coverage. `hold-ready` additionally requires a measurable event return and prices through T+30. These dataset flags never open the independent RUN-UP/HOLD OOS validation gates.

### Starter batch and current-universe audit

The checked-in starter bundle (`mozes/data/historical_starter.json`) contains three reconstructed point-in-time cases: KOD DAYBREAK, QTTB SIGNAL-AA, and PHAR's early Joenja pediatric approval. Source records store normalized fact extracts and their SHA-256 hashes, canonical primary URLs, publication-time basis, and retrieval timestamps. These are audit extracts, not full copies of the source pages. Pre-event snapshots cite only pre-event releases. The fixed CSV price capture in `mozes/data/starter_prices/` is adjusted daily Yahoo Chart data for KOD, QTTB, PHAR, and XBI; its manifest records capture time and file hashes, and `scripts/capture_starter_prices.py` reproduces the capture. The case-level price runs record CSV hashes and the original provider URL. Neither a clean case nor an imported label opens the independent OOS gates.

Run the current security audit before export:

```bash
mozes bootstrap-v2
mozes audit-securities --provider auto
mozes historical-import --file mozes/data/historical_starter.json
mozes historical-prices --case PIT-KOD-DAYBREAK-20260928 --provider csv --stock-file mozes/data/starter_prices/KOD.csv --benchmark-file mozes/data/starter_prices/XBI.csv --source-url https://query1.finance.yahoo.com/v8/finance/chart/
mozes historical-prices --case PIT-QTTB-SIGNALAA-20260713 --provider csv --stock-file mozes/data/starter_prices/QTTB.csv --benchmark-file mozes/data/starter_prices/XBI.csv --source-url https://query1.finance.yahoo.com/v8/finance/chart/
mozes historical-prices --case PIT-PHAR-JOENJA-20260911 --provider csv --stock-file mozes/data/starter_prices/PHAR.csv --benchmark-file mozes/data/starter_prices/XBI.csv --source-url https://query1.finance.yahoo.com/v8/finance/chart/
mozes export-v3
```

`auto` uses the SEC ticker map when `SEC_USER_AGENT` is configured, and the two official Nasdaq Trader current-listing directories otherwise. Nasdaq's financial-status flag remains visible in the listing audit; a deficient flag alone does not mean a share is delisted. Missing from a directory means `UNKNOWN`, never an inferred acquisition. Corporate-action overrides are sourced separately. Pages and the nightly job run this audit before export. Events with elapsed windows or an unconfirmed broad conference window older than 90 days go to the advanced stale queue for reconciliation.

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest -q

# initialize database state, provenance and validation gates
mozes bootstrap-v2

# database-driven current radar
mozes radar-v2 --as-of 2026-09-30

# export Hebrew RTL UI payload
mozes export-v2 --as-of 2026-09-30

# serve locally
mozes serve-v2 --port 8000
# open http://127.0.0.1:8000/index_v2.html
```

## Live refresh pipeline

The intended daily pipeline is:

```text
SEC company map
      |
ClinicalTrials.gov discovery -----> candidate queue
      |                                  |
      |                                  v
      |                          sponsor/ticker mapping
      |                                  |
      +---- SEC/6-K/8-K/EX-99 verification
                       |
                       v
             VERIFIED / SCHEDULED event
                       |
            lifecycle reconciliation
                       |
               scoring + alerts
                       |
                 paper ledger
                       |
             resolved outcome audit
```

Run live refresh:

```bash
# SEC access requires a real SEC_USER_AGENT contact string.
export SEC_USER_AGENT="Your Name your@email.com"
mozes refresh-v2 --months 6
```

If SEC access is not configured, CT.gov discovery can still be run independently:

```bash
mozes discover-v2 --months 6
```

Candidates remain discovery-only until primary-source verification exists.

## Core modules

```text
mozes/
  radar.py            DB-driven orchestration and seed/bootstrap state
  discovery.py        CT.gov Phase 2/3 candidate discovery
  source_quality.py   provenance hierarchy and verification rules
  lifecycle.py        event state machine and terminal states
  refresh.py          SEC map -> CT.gov -> SEC verification refresh pipeline
  promotion.py        conservative candidate-to-event promotion
  session.py          premarket/intraday/after-hours classification
  engine_v2.py        readout/regulatory scoring and gated classification
  market.py           calendar-aligned and session-aware market calculations
  backtest_v2.py      all-grid run-up + session-aware hold-through framework
  audit.py            historical validation eligibility audit
  validation.py       empirical RUN-UP / HOLD release gates
  payload_v2.py       Hebrew UI payload builder
  ingest/edgar.py     EDGAR filings + EX-99 crawling
  ingest/ctgov.py     single-study CT.gov version capture
  ingest/prices.py    CSV / Tiingo price adapters
  paper.py            immutable forward-test ledger
  pit.py              legacy point-in-time guard layer
web/
  index_v2.html       Hebrew RTL v0.2 interface
  data_v2.json        generated payload
```

## Model philosophy

### 1. Event Impact

Measures how much the event could matter to the stock. It is not an estimate of success.

### 2. Clinical Readout Evidence

Uses the relevant phase-transition base rate only as context, then adjusts for prior evidence, endpoint/population continuity, trial design, mechanism validation, safety and integrity risk.

The score is **not a calibrated probability**.

### 3. Regulatory Evidence

PDUFA/AdCom is modeled separately because after pivotal success the remaining uncertainty is often regulatory/CMC/label/inspection-related rather than the original efficacy binary.

### 4. Market Setup

Uses verified point-in-time prices from SQLite and aligns benchmarks by actual trading date. A missing trading date cannot silently shift the benchmark comparison.

### 5. Classification gates

`RUNUP_CANDIDATE` and `HOLD_THROUGH_CANDIDATE` are disabled unless explicit validation gates are unlocked from out-of-sample evidence.

Default thresholds:

- RUN-UP: minimum 150 out-of-sample events plus stable positive walk-forward evidence.
- HOLD-through: minimum 200 out-of-sample events plus a calibrated probability model and positive risk-adjusted utility.

The current seed satisfies neither gate.

## Historical data policy

An event is not eligible to unlock a model unless it passes the audit. Typical disqualifiers:

- features reconstructed after the outcome;
- missing `features_as_of`;
- unverified outcome/move;
- insufficient pre-event price history;
- missing pre-event provenance;
- unknown announcement session when exact event-return timing is required.

This is intentional. A smaller clean dataset is more valuable than a large hindsight-contaminated dataset.

## Primary source policy

Recommended source priority:

1. FDA / Federal Register
2. SEC filings and exhibits
3. Company investor relations / press releases
4. ClinicalTrials.gov for discovery and trial structure
5. Conference sources / peer-reviewed papers
6. Secondary calendars and news only as leads, never as sole actionable verification

SEC provides keyless JSON APIs on `data.sec.gov`, while ClinicalTrials.gov v2 is keyless. SEC fair-access policy still requires a descriptive User-Agent with contact information.

## Current seed corrections in v0.2

- `PHAR-SNDA`: marked resolved/APPROVED because FDA approved the pediatric Joenja expansion on 2026-09-11, before the prior 2026-10-24 PDUFA target date.
- `REGN-POZELIMAB`: quarantined because the seed relied on a secondary calendar and no matching primary PDUFA confirmation was verified during review.
- `ACLX-ANITO`: upgraded to primary-source verified PDUFA date 2026-12-23.
- `CYTK-SNDA`: upgraded to primary-source verified PDUFA date 2026-11-14.

These are examples of the lifecycle/provenance behavior, not hard-coded trading conclusions.

## Testing

The delivered v0.2 passes its complete local test suite, including:

- point-in-time leakage guards;
- lifecycle transitions;
- source verification hierarchy;
- CT.gov discovery-only policy;
- conservative promotion rules;
- SEC-first regulatory discovery;
- announcement-session handling;
- calendar-date benchmark alignment;
- historical audit gate;
- immutable paper ledger;
- legacy v0.1 regression coverage.

Run:

```bash
pytest -q
```

## Next scientific milestone

The next milestone is not more scoring heuristics. It is a **blinded, timestamped, source-provenance historical catalog of 200-500 events with verified prices**, then walk-forward testing and a frozen live paper tape.

Until that milestone is reached, the radar should be used to prioritize research, not to automate capital deployment.
