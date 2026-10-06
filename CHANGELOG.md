# Changelog

## 0.3.2 — hot-lane hardening (v2G)

- Each hot-pass source runs in isolation; one failing feed yields `PARTIAL`, and dispatch always runs.
- Outbox `sending` rows carry a 2-minute lease and are recovered (or dead-lettered) after a crash.
- Feed URLs verified live on 2026-10-06; real samples stored in `tests/fixtures/feeds/`.
  Business Wire / PR Newswire / GlobeNewswire / FDA Biologics URLs corrected; all verified from a
  GitHub runner via the new manual `feed-probe` workflow.
- Nasdaq halts parsed from the `ndaq:` namespace (the old parser accepted zero real items);
  news/regulatory codes vs volatility pauses, identity = ticker + code + halt time.
- P1 only for watchlist tickers; unattributed FDA/wire signals are P3 and not pushed by default
  (`MOZES_ALERT_MIN_PRIORITY`). FDA/wire headlines resolve tickers via company and product aliases.
- Classifier v2: negation/hedge guards, positive+negative = mixed, 8-K/6-K and bare "phase 2/3"
  no longer imply materiality; regression corpus `tests/fixtures/headline_corpus.tsv`.
- Single push owner: Actions workflows no longer receive push secrets; `push_allowed()` guards it in code.
- Cross-source consolidation: the same story for a ticker within 30 minutes is linked (`alert_links`), not re-pushed.
- Worker sleeps to fixed deadlines; feeds send a contact User-Agent.
- Worker deployment: `Dockerfile`, `deploy/mozes-hot.service`, `deploy/hot.env.example`,
  `docs/WORKER_DEPLOY.md`; heartbeat ping (`MOZES_HEARTBEAT_URL`, `/fail` on FAILED), rotating
  online SQLite backups, and a silent warm-up pass on an empty DB. CI builds the image.

## 0.3.1 — breaking-catalyst hot lane (v2F)

- Split GitHub Actions hot vs deep concurrency; hot cron every ~15m on offset minutes.
- Added transactional alert outbox + ntfy/webhook/log push (independent of Pages).
- SEC submissions hot TTL (60s), material discovery retry escalation, IR HTML fallback.
- FDA / wire / Nasdaq-halt primary feed adapters and latency metrics ledger.
- Track CT.gov `primary_completion_type` and `results_first_posted` as imminence signals.
- Deterministic Stage-1 outcome polarity classifier (uncalibrated).
- CLI: `mozes hot-monitor`, `mozes dispatch-alerts`.
- Expanded hot watchlist (~40 names) with official IR sites and verified RSS feed URLs.

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
