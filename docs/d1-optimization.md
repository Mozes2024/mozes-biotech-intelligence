# D1 optimization and safe polling deployment

Implementation baseline: `116d78d9bce29ef94836a47753c656b59c462871`.
Emergency Stage A: `19b7539` (independently deployable; no schema change).
Stage B adds the optimization runtime and migration `0005_d1_optimization.sql`.
No production deployment, secret change, database export, or remote migration was performed during implementation.

## Root causes and evidence

Before optimization, every 15-second alarm loaded the active issuer universe four times (two SEC and two wire scans). Every RSS item queried `edge_candidates`, including unchanged and unresolved items. Matching material wire events repeatedly attempted INSERT and unconditionally updated classification. `/edge/sync` deactivated the entire universe then upserted every issuer. `/edge/health` ran full backlog/candidate aggregates and a recent-event scan on every request. `parseWire` also discarded entries after item 30; SEC only requested the latest 100 filings.

These code paths explain significant excess consumption; the supplied 329,000-query/6M-read production baseline cannot be attributed exactly without historical operation-level telemetry. The new telemetry records query counts, errors and actual D1 `meta.rows_read`/`meta.rows_written` by operation, in Durable Object storage rather than D1.

## Implemented behavior

- The scheduler has a 60-second minimum even under a stale `EDGE_INTERVAL_SECONDS=15` override. The existing `*/2 * * * *` watchdog remains. An instance promise and serialized sync/alarm queue prevent overlapping polling.
- Normal intervals are SEC 8-K/6-K 120 seconds and each wire 180 seconds. Per-source due times, failure backoff, successful-window IDs, high-water times and unresolved gaps survive restarts. Maximum failure backoff is one hour.
- One single-flight issuer cache serves all sources for 900 seconds. Restart reloads D1 once. Committed sync changes immediately invalidate the cache. CIK aliases normalize before hashing; duplicate normalized CIKs are rejected. Snapshot ordering is deterministic in Python and JavaScript.
- Sync applies two conditional JSON-based statements in one D1 transaction. Identical committed snapshots perform zero D1 operations. Changed snapshots update/insert/deactivate only changed records. A failed transaction does not publish its hash or invalidate the cache.
- Successfully persisted RSS/SEC items have a 48-hour, 12,000-entry bounded cache. Changed content, issuer universe or classification rules reprocess RSS items. ETag/Last-Modified validators are accepted only after processing succeeds; issuer/policy changes force an unconditional fetch. The rule-set contents are part of the policy fingerprint.
- Hashed RSS GUIDs (URL fallback) are durably linked to the existing candidate/event identity, including after dedup eviction and publisher URL changes. Legacy URL-based IDs remain valid. The nullable source-item column is filled without reopening an otherwise unchanged completed candidate.
- SEC discovery uses bulk INSERT, and wire candidate/event/classification changes use an atomic three-statement bulk transaction per chunk. Cache completion follows the transaction. Changed evidence can upgrade/downgrade unacknowledged events; unchanged classifications do not write equivalent rows. ACKed events and event-linked candidate completion receipts retain their delivery history and remain closed.
- RSS parsing includes all returned items, bounded to 3,000 items and 1 MiB XML. Payloads over 1.8 MB fail explicitly and remain retryable. SEC walks up to ten 100-item pages and detects missing overlap or repeated/unexhausted windows. Gaps trigger durable, bounded per-issuer SEC submissions/archive backfill (three issuers and up to three relevant archive files per issuer per pass). Backfill waits while analysis work is active.
- Alarm analysis normally processes up to 12 filings/minute, retaining the old three-per-15-second queue capacity. It runs three network analyses concurrently, batches the results atomically, and reduces the batch during heavy feed pagination to reserve external-request capacity for delivery. Failed analyses retain their retry receipts.
- Expensive health summaries are cached five minutes (15 minutes in ECO, one hour in PROTECTION), including across restarts; source/alarm indicators remain fresh. Public alert GETs share a five-second in-memory cache; successful publication invalidates it. Existing authentication, CORS, enrichment dispatch claims and durable completion ACKs remain.
- Automatic candidate-history pruning is removed in Stage B. Only dedup state expires; event/alert history is never deleted by this change. Candidate statistics now report unlimited retention rather than claiming the former 14-day/3,000-record pruning policy.

## Quota guard and coverage

The guard uses measured Worker-local D1 metadata plus configurable allowances for unmeasured requests and other account consumers. It explicitly reports `account_usage_available=false`; no trusted account-level Cloudflare analytics capability was available. The configured outside-consumer reserves are 1M reads/day and 10K writes/day, not measured account usage.

NORMAL uses the standard profile. Above a projected 40% of either read or write limit, ECO doubles wire intervals and extends summary caches. Above 60%, PROTECTION doubles SEC intervals, quadruples wire intervals and further extends summary caching. ECO exits below 30%; PROTECTION exits below 50%. Durable ingestion/analysis queues, ACKs and essential delivery remain enabled. Health shows mode, estimates, largest measured consumers, source intervals and continuity gaps; it contains no feed bodies, raw source-item IDs or credentials.

The timestamp-based projection starts at the observed telemetry period, with a one-hour minimum denominator and a partial-day label. Counters reset at UTC midnight. Lost checkpoint telemetry/cold starts and outside consumers can undercount account usage; the reserve cannot guarantee account-wide headroom. Correlate the estimates against account analytics before treating quotas as validated.

SEC gaps clear only after all cached-universe issuers complete submissions/archive recovery. RSS feeds do not offer a verified archive-pagination API here. An RSS gap stays `DEGRADED`, with `REQUESTED_UNVERIFIED` reconciliation status after a fallback Actions dispatch. Successful HTTP polling or a 204 dispatch does not clear it. Existing 15-minute Actions reconciliation remains unchanged, but its recent feeds cannot prove recovery of a publisher item that disappeared from all available windows. Publisher archive/manual evidence is required for those gaps; the PR does not claim guaranteed complete wire history. Restart downtime and first-start history are similarly bounded by upstream availability.

## Reproducible measurements

Run from `cloudflare` with Node 24:

```sh
npm ci
npm test
npm run profile
npm run benchmark
npx wrangler deploy --dry-run --outdir .wrangler/dry-run
```

The benchmark needs the pinned baseline Git object; CI checks out full history. `BENCHMARK_BASE_REF` can explicitly override it. `BENCHMARK_OUTPUT` and `PROFILE_OUTPUT` select JSON report paths.

See [24-hour workload](d1/workload-24h.json) and [query plans](d1/query-profile.json). The local workerd D1 fixture has 200 issuers, 30 items per wire, two new material wire items/hour, 48 new SEC filings/day, one health request/minute and 96 identical issuer snapshots. It executes 1,440 optimized alarms and weights only measured unchanged baseline ticks. Enrichment is disabled in this volume fixture; its failures, dispatch limits, ACKs and deduplication are exercised separately in the regression suite.

| Daily workload | Baseline code, local D1 | Optimized, local D1 |
|---|---:|---:|
| Queries | 578,314 | 5,906 |
| Rows read | 6,213,182 | 220,169 |
| Rows written, including indexes | 89,464 | 1,442 |
| Read limit utilization | 124.26% | 4.40% |
| Write limit utilization | 89.46% | 1.44% |

Query reduction is 98.98% in this fixture; the optimized query count is 98.20% below the supplied 329K production baseline. That cross-baseline percentage is arithmetic, not a measured production reduction. The synthetic baseline differs from the supplied production workload and is not calibration to it. The fixture plus configured outside-consumer reserves would estimate 1,220,169 reads/day (24.4% of the limit) and 11,442 writes/day (11.4%); actual external consumers and enrichment must be measured.

Normal polling wait bounds conservatively increase from 15 seconds to 180 seconds for SEC and 240 seconds for wires (interval plus scheduler quantization). These are calculated polling bounds, not measured end-to-end material-event latency. Upstream lag, alarms running longer than their interval, network/analysis delays, retry backoff, backlog, reconciliation and ECO/PROTECTION add delay. The scheduler rearms after completion, so processing time also shifts the cadence. Source latency percentiles remain available through `/edge/metrics`.

On a separate 10,000-record historical fixture, candidate pending reads fell from 10,003 to 3, recent metrics from 10,100 to 101, and an empty enrichment queue from 9,999 to zero. Existing candidate primary-key and analysis/enrichment indexes were retained. Three performance indexes are added; the fourth unique source-item index enforces stable identity. No proposed analysis index without measured improvement was retained. Whole-history counts still scan; caching and the quota guard reduce their frequency rather than fabricating exact incremental totals.

## Changed files

- Runtime: `cloudflare/src/hot-edge.js`, `cloudflare/src/edge-runtime.js`, `cloudflare/src/index.js`, `cloudflare/src/candidate-contract.js`, `cloudflare/src/clinical-events.js`.
- Configuration/schema: `cloudflare/wrangler.toml`, `cloudflare/migrations/0005_d1_optimization.sql`, `cloudflare/package.json`, `cloudflare/package-lock.json`.
- Compatibility/CI: `mozes/edge_sync.py`, `.github/workflows/ci.yml`. The lightweight-monitor workflow is inspected and preserved.
- Verification: `cloudflare/test_hot_edge.mjs`, `cloudflare/test_hardening.mjs`, `cloudflare/test-db.mjs`, `cloudflare/test_optimization.mjs`, `cloudflare/benchmark_d1.mjs`, `cloudflare/profile_d1.mjs`.
- Reports/runbook: `docs/d1/workload-24h.json`, `docs/d1/query-profile.json`, `docs/d1/rollback-stage-a.patch`, `docs/d1-optimization.md`.

Miniflare and Wrangler are pinned development-only tools; no application runtime dependency is added. The patched Miniflare release carries an upstream alpha tag and matches Wrangler's workerd generation. Local `npm audit` reports zero vulnerabilities. No global installation was used.

## Verification

The Python suite passes (472 tests). `npm test` passes the original watchdog, alert-feed, materiality, clinical, SQLite retry/ACK suites and the new cache/sync/restart/failure/burst/continuity/concurrency/budget regressions. A cold alarm with 145 SEC filings and 65 RSS items exercises fewer than 50 D1 queries and completes 12 filing analyses. The additive migration preserves a preexisting event and completed candidate byte-for-byte except for the new nullable source-item column. Python compile checks, web JavaScript syntax checks, local D1 profiling/benchmark and Wrangler dry-run pass. GitHub CI also runs the workload and query-plan tools; its result is reported with the PR rather than inferred from local tests.

No production-source stress test or authoritative account quota test was run. Very large/oversized responses, long historical archive gaps, high enrichment failure rates and large accumulated history can exceed the representative workload. Platform invocation/CPU/subrequest limits require production validation under those conditions. The original Stage-0 ntfy delivery remains at least once: a remote notification can succeed before its D1 receipt fails. Stable event IDs and ACK/claim predicates prevent routine repeats, but exactly-once notification across that crash window is not claimed.

## Deployment: approval required

**Production hold:** do not merge the readiness PR, apply migration 0005 or deploy without explicit approval. The dashboard workaround is already active at `EDGE_INTERVAL_SECONDS=120`; preserve 120 during initial rollout. Wrangler now explicitly declares 120: `keep_vars` alone does not protect variables also declared in the configuration. Reducing the scheduler requires separate approval.

### Mandatory backup and migration gate

After approval, use the reviewed PR commit in a clean checkout. Record the SHA, current Worker deployment/version ID, UTC time, nonsecret effective environment, database counts and completed ACK receipts. From `cloudflare`, run `npm ci`, `npm test`, and `npx wrangler deploy --keep-vars --dry-run`; require scheduler 120 and the expected database binding/ID.

Before any migration, export the entire database outside the repository to an access-controlled directory and record a current Time Travel bookmark. These commands are for the approved production operation only:

```powershell
$backupDir = Join-Path $env:LOCALAPPDATA ('mozes-d1-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backupDir
npx wrangler d1 export mozes-alerts --remote --output (Join-Path $backupDir 'before-stage-b.sql')
if ($LASTEXITCODE -ne 0) { throw 'D1 backup failed; stop rollout' }
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $backupDir 'before-stage-b.sql')
npx wrangler d1 time-travel info mozes-alerts --json
npx wrangler d1 migrations list mozes-alerts --remote
```

Require a nonempty export, successful commands and a recorded bookmark. Test restoration in an isolated local SQLite database; compare schema, migration ledger, table counts and representative completed ACK/history receipts to the export. Never restore into production as a test. Failed backup/restore verification blocks rollout. Time Travel retention depends on the account plan; do not assume indefinite availability. References: [D1 export](https://developers.cloudflare.com/d1/best-practices/import-export-data/), [Time Travel](https://developers.cloudflare.com/d1/reference/time-travel/).

Require migrations 0001–0004 already applied and **only 0005 pending**. Stop on unexpected pending migrations rather than applying the directory blindly. After approved application, verify `d1_migrations`, `PRAGMA table_info(edge_candidates)` (`source_item_id` nullable), and all four index names/predicates in `sqlite_master` against `0005_d1_optimization.sql`. Compare event/alert/candidate counts and completed ACK receipts before/after, accounting for concurrent legitimate writes. Any unexplained discrepancy blocks deployment. No readiness migration is added; never manually repeat the ALTER TABLE.

### Effective environment checklist

- Verify Worker `mozes-hot-clock`, `ALERTS_DB` ID `9baf99f2-1ccc-4415-affd-2708a7210e7b`, existing `HOT_EDGE` namespace/class and cron `*/2 * * * *`.
- Compare the reviewed configuration, CLI overrides, dashboard and actual deployed Worker settings. Explicit configuration can override dashboard values. See [Cloudflare deployment options](https://developers.cloudflare.com/workers/wrangler/commands/workers/).
- Require scheduler **120**, SEC 8-K/6-K **120**, wires **180**, issuer TTL **900**, health TTL **300**, SEC pages **10**, backfill issuers **3**, analysis batch **12**, and documented external-usage reserves. ECO/PROTECTION may extend source intervals.
- Verify `GITHUB_REPO`, `GITHUB_REF=main`, `WORKFLOW_FILE`, `EDGE_ENRICHMENT_ENABLED=1`, `SITE_ORIGIN` and valid `SEC_USER_AGENT`. Check secret names/bindings without displaying values or overwriting credentials.
- Observe `/health` for at least two completed cycles: alarm cadence at least 120 seconds plus processing time, expected source effective intervals, successful polling/analysis and ACK traffic. Dashboard values alone are insufficient evidence.

### Reliability validation

ACKs for durably claimed/dispatched work remain acceptable after newer RSS evidence downgrades the event. Completion closes retries and records `SUPERSEDED` in candidate history without reviving delivery eligibility. Resending the identical ACK repairs partial receipt/history failure; conflicting change IDs return 409. ACK handlers share alarm serialization. Updated evidence retains completed event-linked candidate receipts.

A policy JSON/cache-version digest triggers a resumable scan of all stored SEC events, 100 IDs per cycle, independent of recent feeds. Unacknowledged completed filings with missing/different digests are requeued. Normal bounded analysis re-fetches primary evidence with failure backoff and commits candidate, classification, digest and analysis receipts atomically. Completed ACKs remain excluded; pending/failed work uses current policy on its next analysis. Progress advances only after D1 succeeds, and replay after lost progress is idempotent. Changed rule JSON triggers reassessment even with the same human version.

During rollout verify scan completion, backlog age and additional historical-pass D1 reads (not included in steady-state savings). Sample an old upgraded filing, a downgraded filing, an unchanged filing and a completed ACK; ensure receipts/history remain and alerts are not duplicated. Observe an in-flight RSS downgrade followed by completion ACK: retry state must close while suppression remains. Continue the existing 48-hour quota/coverage checklist.

`/health.sec_policy_scan.complete` means the scan finished scheduling work, not that its analysis backlog drained. Check both. The scan pages across the event primary key (including non-SEC IDs) to bound reads without a new index; it only requeues SEC rows. At scheduler 120, batch 12 gives at most six analyses/minute before processing time and retries. Rule JSON changes invalidate automatically; changes to classifier code outside that JSON must bump `POLICY_VERSION`. Already-sent Stage-0 notifications and alerts produced by in-flight Actions cannot be recalled by a later downgrade. Completion records their provenance and prevents repeats; it does not claim exactly-once external delivery.

Rollback uses the existing history-preserving Stage A patch, with an explicit `--var EDGE_INTERVAL_SECONDS:120` for both dry-run and deploy. It preserves the additive schema and existing data. Stage A restores older ACK/reclassification behavior, so pause the rollout, record outstanding completions and reconcile them after redeploying the corrected Worker. Database restore is disaster recovery only: isolate writes, record/export post-backup ACKs and alerts, obtain separate restore approval, restore the recorded bookmark or validated export, then reconcile post-backup records before resuming. Restoring an old database can otherwise repeat alerts or lose ACKs.

Record the existing Worker version, effective dashboard variables, account D1 daily totals and database row counts before changes. Do not print or change secrets. Stage B sets `keep_vars=true` to preserve dashboard-only configuration (including SEC identity if configured as a nonsecret); the Stage A commands explicitly use `--keep-vars`. Inspect explicit polling variables after deployment because retained dashboard values are not proof of effective scheduling. The binding must remain `ALERTS_DB`, database `mozes-alerts`, ID `9baf99f2-1ccc-4415-affd-2708a7210e7b`; the Durable Object class/namespace and migration tag remain unchanged.

Stage A can be deployed first from a separate checkout of `19b7539`:

```sh
git worktree add ../mozes-edge-emergency 19b7539
cd ../mozes-edge-emergency/cloudflare
npx --yes wrangler@4.149.0 deploy --keep-vars --var EDGE_INTERVAL_SECONDS:120 --dry-run
# Only after explicit production approval:
npx --yes wrangler@4.149.0 deploy --keep-vars --var EDGE_INTERVAL_SECONDS:120
```

The emergency workaround is already active; no Stage A deploy is needed for this readiness task. If this checkout is later used, require scheduler 120 and apply the history-preservation patch under Rollback. The two-minute watchdog remains unchanged.

Stage B requires migration 0005 before the new Worker (its queries reference `source_item_id`):

```sh
cd <approved-Stage-B-checkout>/cloudflare
npm ci
npm test
npx wrangler d1 migrations list mozes-alerts --remote
# Inspect the list. Existing 0001-0004 must already be applied.
# Require only 0005 pending. Complete mandatory backup/restore verification above.
# Only after explicit production approval:
npx wrangler d1 migrations apply mozes-alerts --remote
# Verify migration ledger, nullable column, four indexes and preserved ACK/history.
npx wrangler deploy --keep-vars --var EDGE_INTERVAL_SECONDS:120
```

Migration 0005 adds one nullable column and four indexes, without changing stored event/alert data. Do not manually rerun its ALTER TABLE statement after it has been recorded as applied. Leave applied migration records intact. No new secret, Durable Object namespace migration, or Actions input is required. Run the existing issuer-sync workflow once; verify the next identical sync returns `unchanged=true`, `changed=0`. Check that the normal interval variables in `wrangler.toml` match deployed values, including 900-second issuer TTL, 300-second summaries and analysis batch 12.

## 48-hour production validation

- At rollout, save version/row-count snapshots and account daily usage, inspect `/edge/health`, and verify alarms continue without overlap and the watchdog rearms after a restart. Repeated health calls should retain the same summary `generated_at` for five minutes while source/alarm timestamps move.
- In the first hour, confirm active issuers/ticker/CIK semantics, zero writes for identical sync, single-row changes for a small approved universe update, immediate cache invalidation, and normal source success times. Compare unchanged RSS passes with `item_hits` and operation counters; verify no repeated candidate lookups/writes on warm passes.
- For sampled material events, trace source publication/acceptance, discovery, classification, Stage-0 delivery, Actions enrichment, authoritative publication and completion ACK. Confirm ACKed events stay out of dispatch retries and candidate history remains present. Compare actual p50/p95/p99 latency with the desired operational latency before accepting slower polling.
- Across both UTC quota days, check **account** totals against <1.25M reads/day and <20K writes/day and at least 75% read headroom. Separately compare Worker-local metadata and external reserves; identify unexplained differences and adjust reserves upward. Confirm no per-tick D1 telemetry writes.
- Observe mode transitions/hysteresis with existing health telemetry. ECO/PROTECTION must expose longer effective intervals, keep ACK/essential delivery usable and show delayed/degraded coverage. Check retry deadlines and queue age; do not regard low usage caused by failures as success.
- Exercise or observe a >100-filing SEC burst and >30-item wire feed. Verify all available items persist, SEC pagination/backfill cursors advance and failed D1 work resumes. Investigate every RSS continuity gap against publisher/issuer archives; unresolved gaps block a full-coverage sign-off.
- At 24 and 48 hours, compare event/alert counts and representative history/ACK rows to baseline; check duplicate notifications, retained candidate history, database growth, restart frequency and aggregate scan cost. Accept the optimization only when quota and coverage/latency criteria both hold.

## Rollback

Prefer the temporary 120-second profile if quota pressure remains high while correctness is intact. For a Stage B correctness regression, record the incident/gaps and deploy Stage A **with the supplied history-preservation patch**, rather than restoring its legacy pruning. The patch was applied against `19b7539` in an isolated checkout; its JavaScript suite and Wrangler dry-run pass. The prepared local rollback checkout is `C:\Users\Moshe\Documents\mozes-biotech-edge-rollback`.

To reproduce from the approved Stage B repository root in PowerShell:

```powershell
$rollbackPatch = (Resolve-Path ./docs/d1/rollback-stage-a.patch).Path
git worktree add ../mozes-edge-rollback 19b7539
Set-Location ../mozes-edge-rollback
git apply --check $rollbackPatch
git apply $rollbackPatch
Set-Location cloudflare
npm test
npx --yes wrangler@4.149.0 deploy --keep-vars --var EDGE_INTERVAL_SECONDS:120 --dry-run
# Only after explicit production approval:
npx --yes wrangler@4.149.0 deploy --keep-vars --var EDGE_INTERVAL_SECONDS:120
```

Retain 120-second scheduling. Additive column/indexes remain; old code ignores them. Do not drop tables/indexes/columns, restore the database to an old snapshot, remove the Durable Object namespace, rewind Actions artifacts or clear ACK/history rows. Schema rollback is unnecessary and would risk history. Rolling back to the original 15-second Worker is not an acceptable default during quota exhaustion. Revalidate account usage, effective intervals, fallback Actions and ACKs immediately. Before redeploying Stage B following any direct legacy/manual issuer edits, POST the reviewed verified issuer snapshot to the authenticated `/edge/sync` endpoint with the additional JSON field `"force":true` so the optimization hash cannot hide an out-of-band universe change; do not claim that the cached hash detects external edits.
