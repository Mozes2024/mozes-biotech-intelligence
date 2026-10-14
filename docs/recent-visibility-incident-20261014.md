# Recent update visibility incident — 2026-10-14

The confirmed regression is the modern browser consuming an intentionally filtered legacy alert feed. It erases the complete alert list already present in `data.json`. The inspected checkpoints retain historical records, and the current seven-day changes export is populated. An independently empty “מה השתנה?” section was not reproduced.

## Evidence and first divergence

Investigation base: `main` at `55b83ba126f994cd2d49124d8387e2bd3d874934`. Hub instructions: 1.2.0 at `3f25796a80ae38021cd2c9149df78f2b0df2a288`. Production files were downloaded on October 14, around 12:10 Jerusalem time. Counts below describe those captured versions, not indefinitely current live values.

| Layer | Complete alerts | Legacy alerts | Recent changes |
| --- | ---: | ---: | ---: |
| October 13 23:57 Jerusalem checkpoint, run 38466618621 | 25 | 0 | 535 eligible, newest 100 exported |
| October 14 12:00 Jerusalem checkpoint, run 38539043375 | 27 | 0 | 544 eligible, newest 100 exported |
| October 14 Pages artifact, run 38539270756 | 27 | 0 | 100; news subset capped at 50 |
| Captured deployed `data.json` | 27 | — | 100: 90 detected October 13 UTC, 10 October 14 UTC |
| Captured deployed `alerts-evidence-v1.json` / `alerts.json` | 27 | 0 | — |
| Actual deployed browser code, controlled Worker outage | **0 displayed after polling** | 0 consumed | 100 retained |
| Corrected browser code, identical captured inputs/outage | **27 displayed after polling** | not consumed | 100 retained |

The initial snapshot's alerts and the complete static feed have the same 27 records. Six alert records carry an October 13 UTC detection date. All historical snapshot alerts are list-only under the complete interrupt eligibility contract, so restoring visibility must not synthesize popups.

`updates.json` advertises revision `abfa4dfbb90e985fd849024a172bac01a54ebccca31d8282c9476f845664c955` and the same checkpoint identity as the published snapshot. The artifact and deployed data agree on record counts, checkpoint identity, snapshot revision and change ledger. Both static alert projections carry revision `b2f74a33c0a824fe265fff978d1bf97ebdb43364e5dcf3000e784d213e372d7d` despite different row counts: revision equality is not proof of list completeness.

The causal path is:

1. `mozes.alert_dispatch.recent_alerts()` reads retained Stage-1 outbox records without requiring popup eligibility.
2. `mozes.payload_v3.build()` exports that complete list into `data.json`, and independently exports `recent_changes()` into `changes`.
3. Since PR #34, `mozes.pipeline_v2c.export_payload()` writes the full feed to `alerts-evidence-v1.json` and `browser_feed(feed)` to `alerts.json`. `mozes.alert_feed.browser_feed()` intentionally restricts legacy clients to fully eligible interrupts. All 27 captured records fail that eligibility contract, so this projection is empty. **This is the first confirmed count divergence.**
4. `web/alerts_ui.js:checkForAlerts()` still requests the legacy Worker route, then `alerts.json` on outage. It accepts the empty projection.
5. `preserveAlertFeed(D)` replaces the complete snapshot's 27 alerts with zero. `preserveAlertFeed(next)` can repeat the replacement during open-tab snapshot refresh. This affects Alerts and dashboard alert summaries. It does not assign `D.changes`.

## Historical lineage

PR #34 merged on October 13 at 13:51:10 Jerusalem time (`e98cb3c25b6c3a427bb06cd55b4189d72bf82762`). Immediately surrounding successful Pages runs are:

- [Before #34: 38385865928](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38385865928), code `6f939b8299a5d96dc4e175e5e75baf1ec5636080`.
- [After #34: 38386457777](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38386457777), code `e98cb3c25b6c3a427bb06cd55b4189d72bf82762`.

Their publication request and successful publication ledgers refer to **the same** producer `38385514066`, artifact `11805252388`, checkpoint `2026-10-13T10:48:08+00:00`, DB SHA256 `e11100eb24dc9ecaab91a81e332ccc3273f194818a0d57a30559daee47a8a64b`, and change ledger `db40fba176eaa262578a76998fe820e87215cfa059a5f0ac8e1a5c9948ba6707`. The checkpoint contains 1,262 change events, 46 outbox rows, and 46 alert rows in the then-current seven-day window. Reading the two export implementations against those rows explains a complete 46-row pre-compatibility fallback becoming a protected zero-row legacy fallback, with 46 rows still available to a modern client. This historical count comparison is a **read-only reconstruction**, not a claim that an expired October 13 Pages tar was downloaded.

Additional successful runs inspected:

- [October 13 local-day monitor: 38466618621](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38466618621): 1,373 change rows; 50 outbox rows; hash `cc2bfecdfcb14db8c9fb757ac58f043bf97eb3b7be284b5a9fd96cb8dc1ec4f5`.
- [October 13 local-day Pages: 38461928710](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38461928710): ledger/checkpoint references producer `38461611626`, hash `a57a7fac7a11f66a13769a38c19127a068881cdb4121cda90a49b80078f45dce`; recovered source checkpoint has 1,372 change rows and 50 outbox rows.
- [October 13 UTC final monitor: 38487510573](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38487510573): 1,384 change rows and 52 outbox rows. Its checkpoint completed October 14 00:01 UTC, so it is not represented as an October 13 Jerusalem checkpoint.
- [October 13 UTC final Pages: 38485787408](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38485787408): matching request/acknowledged ledgers, producer `38485544910`, hash `1ec3e9ec4ae4f40ba10fe38a28a86f5aea5d485c0660872a7cbe17eb7204094b`.
- [October 14 monitor: 38539043375](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38539043375): 1,394 change rows; 52 outbox rows; hash `f4f7b89e31b39b95fe671b7118986e98541e0d692d2b07f12919ed528e03467f`.
- [October 14 Pages: 38539270756](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38539270756): recovered tar, request and successful publication ledger bind to that exact October 14 checkpoint. Change ledger `5a47d15d39a9c775736b9f7afe87aa8c815498c77fc3731c7a429e9e26b6e390`.

The repository's existing recovery artifact layout and SHA-bound inspection were used. SQLite connections used `mode=ro`; database SHA256, `PRAGMA integrity_check`, and `foreign_key_check` passed. Full-row comparisons show zero lost or modified historical `change_events` between the pre-#34/October 13 checkpoints and October 14. The 44 sent pre-#34 outbox receipts and 48 sent October 13 receipts are byte-for-field identical in October 14's outbox. No checkpoint was restored into a runtime or production database.

Some Pages runs failed despite surrounding successful runs. Inspected failure [38536002595](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38536002595) failed at restore selection with `producer_busy`, before export/publication. This is evidence of the existing no-rewind protection, not evidence that that run published an empty database.

## Minimal correction

Only browser code, asset cache versions, regression tests and this report change:

- Negotiate `client=alert-evidence-v1` for both Worker and local API requests. Static fallback is `alerts-evidence-v1.json`.
- Reuse PR #35's interrupt eligibility function and popup/system notification guards. The same complete A5 field contract applies to interrupts; ordinary list rows remain visible. Notification storage key, baseline behavior, cross-tab lock and change-ID dedup are preserved.
- Validate feeds before accepting them. Invalid timestamps, malformed envelopes, explicitly truncated feeds and omission of known unexpired alert IDs cannot erase a complete snapshot. An invalid/incomplete Worker response attempts the complete static fallback. IDs age out according to the existing seven-day **outbox creation-time** window; no records are synthesized.
- A newer complete snapshot does not inherit an older independent feed. Existing checkpoint identity, ordering and revision checks in `checkForUpdates()` remain unchanged.
- Current production coverage is `degraded`, with failed/partial source checks. Existing unconditional zero-state wording incorrectly implies no events occurred even when data/coverage is missing or stale. Empty Alerts/changes messages now distinguish those cases from zero records collected in checked sources.
- No collection, export query, DB schema, Worker deployment, A5 classification/activation, B1 logic, scoring, trading gate or delivery receipt changes.

The feed-completeness guard is deliberately conservative: a feed which omits a known still-current ID, including a bounded/truncated projection, keeps the last known complete list rather than treating absence as deletion. Such feed failures do not imply complete current coverage; the captured snapshot remains the evidence. A first feed without any known snapshot IDs cannot establish completeness from an absent capability marker alone.

## Regression validation

The seven new focused Node tests were run against the unfixed frontend and all seven failed, including legacy URL selection, snapshot erasure, missing interrupt eligibility and misleading empty wording. They pass after the correction.

Passing checks:

- Final focused Python alert/feed/live-monitor/checkpoint/publication/lineage/news suite: **193 passed** in 58.62 seconds.
- `node --test tests/test_open_tab_refresh.mjs tests/test_dashboard_integration.mjs tests/test_recent_visibility.mjs`: 103 tests. Includes real final script-chain rendering of October 13 records on October 14, reload and open-tab refresh, older checkpoint/conflicting identity rejection, modern fallback, list-only visibility, complete interrupt contract and once-only notification.
- `node --test cloudflare/test_a5_feed_contract.mjs cloudflare/test_alert_cache.mjs`: 22 tests, including protected legacy browser behavior, full modern feeds and client-isolated cache reads. `PYTHON` points to the task-local interpreter for offline export tests.
- `node cloudflare/test.mjs`, `node cloudflare/test_alert_feed.mjs`, `node cloudflare/test_hot_edge.mjs`: passed, including Worker/DO/D1/ACK/cache/fairness and migration contracts.
- `node cloudflare/profile_d1.mjs`, `node cloudflare/benchmark_d1.mjs`, `node cloudflare/benchmark_fairness.mjs`: passed; existing local query-plan and synthetic workload checks, not production metrics.
- `python -m compileall -q mozes scripts`, all `web/*.js` syntax checks and `git diff --check`: passed.
- Offline bootstrap/export smoke succeeded against a new local scratch DB. Generated tracked `web/data.json` was restored to the branch base; generated feeds/receipt were excluded from the diff.

The full local Python suite ran: **1,039 passed, 2 failed** in 431 seconds on Windows/Python 3.12.14. One failure was missing Windows IANA timezone data; installing `tzdata` only in the task-local environment made the unchanged timezone test pass. The other, `test_background_refresh_returns_run_id_and_sanitizes_failure`, fails its one-second wait both on this branch and on a separate clean worktree at unchanged `55b83ba`; no assertion or production code was weakened. The repository's Linux/Python 3.11 PR CI is the additional full-suite and Docker packaging verification; consult the PR's exact-head CI status.

## Production verification after separate review and deployment

1. Confirm the published `index.html` loads `recent-visibility-v1` for the changed browser scripts, then inspect the actual loaded `alerts_ui.js`.
2. Capture `data.json`, `updates.json`, `alerts.json` and `alerts-evidence-v1.json` from one deployment. Verify snapshot revision, source checkpoint identity and publication ledger agree. Compare IDs, not only counts.
3. From a network that can reach the Worker, GET `/alerts` and `/alerts?client=alert-evidence-v1`. Legacy rows must all satisfy the interrupt contract; modern rows must retain list-only alerts. Worker and Pages may advance at different times, so compare generation times and known IDs.
4. Open Alerts and “מה השתנה?”, wait through a 30-second alert poll and a minute snapshot poll, then reload. Retained October 13 records within the seven-day window should remain visible. Changes are an existing newest-100 list, not an uncapped ledger.
5. Block the Worker request in browser developer tools. Confirm the fallback request is `alerts-evidence-v1.json`, known list-only alerts remain visible, and no false popup or system notification is generated.
6. Observe a real future fully eligible alert: it should notify only once across tabs/reload. Verify this in an isolated test environment if no real eligible event arrives; do not create or replay production events for validation.
7. Use the automated fixture for older-checkpoint rejection and degraded/missing/stale zero-state wording. Do not manipulate production checkpoints to test ordering.

## Explicit limitations

- Direct Worker legacy/modern live responses are unverified: this computer's corporate network returned an access-denied page, and the web reader could not reach either URL. No actual Worker counts are claimed. Worker contracts were verified offline.
- October 13 historical Pages tars were no longer available in the downloaded artifact inventories. Their retained successful publication ledgers and SHA-bound producer checkpoints were inspected; historical feed counts are reconstructed from retained rows/code.
- The user's exact October 13 browser cache/feed and the precise October 14 observation time are unavailable. The deployment transition plus current captured-data replay proves the alert visibility mechanism; it does not prove the first user-visible minute of the incident.
- No independent zero-change export was found. The “מה השתנה?” zero report remains unverified. Current records persist and render; the pre-existing newest-100/50 limits can omit older in-window rows and are unchanged.
- No production merge, deployment, workflow trigger, database/DO mutation, secret/variable/configuration change, A5 activation, Deep Monitor restart or historical replay was performed.
