# Changelog

## 0.3.0 — product layer

- Added a local stdlib JSON API and `mozes app` command.
- Rebuilt the Hebrew RTL interface as a daily-use SPA with dashboard, Radar filters, event detail drawer, candidates, resolved events, paper signals, validation and system status.
- Added Paper Signal creation from v0.2/v0.3 analyses and fixed the legacy `classification.cls`/`classification.class` mismatch.
- Enriched live event payloads with company, indication, phase, features and as-of metadata.
- Added v0.3 product payload/summary and static export via `mozes export-v3`.
- Added automated GitHub Pages deployment with optional live SEC refresh.
- CI now validates v0.3 assets and product export.
- Test suite: 81 passing tests at release build time.


## 0.2.0 - 2026-09-30

### Added
- Database-driven v0.2 radar.
- Source-quality verification hierarchy.
- Catalyst lifecycle state machine.
- CT.gov discovery-only pipeline.
- SEC company-map refresh.
- SEC filing + EX-99 extraction.
- Conservative CT.gov candidate promotion from primary statements.
- SEC-first PDUFA/AdCom discovery for the watch universe.
- Separate clinical readout and regulatory evidence engines.
- Session-aware event-return calculations.
- Calendar-date benchmark alignment.
- Historical validation eligibility audit.
- Locked empirical release gates for RUN-UP and HOLD-through.
- v0.2 Hebrew RTL UI and payload exporter.
- Full-grid v0.2 backtest framework.

### Corrected seed state
- PHAR pediatric Joenja event resolved early as APPROVED.
- REGN pozelimab/VEXAS seed row quarantined pending primary confirmation.
- ACLX anito-cel PDUFA upgraded to primary-source verified.
- CYTK supplemental PDUFA upgraded to primary-source verified.

### Validation
- 79 local tests passing at release time.
