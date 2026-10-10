# Production-compatible fairness candidate — deployment hold

Base: `116d78d9bce29ef94836a47753c656b59c462871`, proven byte-identical to deployed Worker version `350b5194-e379-4e14-bf5a-6c17da9c5c41` on 2026-10-10.

Runtime changes are restricted to removal of destructive candidate pruning and the reviewed PR #16 fairness/atomic three-active guards. Original interval formula, alarm execution, start-based rearming and source/ACK/feed modules are unchanged. Configuration declares the effective 120-second interval and keep_vars; all other bindings/variables/cron/DO migration tag remain unchanged. No Stage B runtime or migration 0005 exists in this candidate.

`npm test --prefix cloudflare` runs the full original Worker suites, production-baseline regression and fairness regressions. Baseline regression compares alarm code exactly (normalizing only line endings and removing the approved pruning block), verifies original 15-second fallback/10-second minimum, short/overlong alarm rearming, 3,005 retained old candidates and historical event/ACK, existing DO state/alarm compatibility and unchanged other runtime modules/migration files. Fairness tests cover the six retained identities, rotation, growing arrivals, restart, deadlines, active cap, concurrent claims, retries and idempotent ACKs. External receipt delivery remains protected by the unchanged authoritative monitor on main; its current source-cooldown, stable-change/receipt, ACK-after-upload and browser-only regression tests passed separately.

The dedicated `production-candidate-checks` workflow runs offline Worker tests with read-only GitHub permissions on these two review branches only. Its name does not match the Pages workflow's `ci` trigger. Publishing these branches does not deploy a Worker/website or process alerts. The existing production monitor stays on main.

Protected evidence outside Git records exact commit and module hashes, production metadata, local workerd D1 measurements and dry-run output. The production-size model has 27 records (21 retained + six blocked pending), with 120-second ticks/900-second ACK deadline. Measurements concern selection/claim work only; DO cursor writes are outside D1 and existing large-history legacy index scans remain.

## Approval boundary and proposed operation

Do not deploy this branch without separate explicit approval. Immediately before an approved operation, revalidate deployed version/content, interval 120, all existing secret names, ALERTS_DB database identity, HOT_EDGE namespace/class, migration tag hot-edge-v3, cron and schema 0001–0004. Stop on drift. Verify the immutable reviewed commit and compiled bundle hash, run the complete tests and pinned Wrangler 4.149.0 dry-run with keep-vars and explicit EDGE_INTERVAL_SECONDS:120. Deploy only this isolated configuration, never current main's Stage B Worker. Apply no migration.

Observe automatic cycles only: all six pending events must receive fair eligible opportunities; no more than three active enrichments; preserve retry deadlines/primary-source cooldowns. HTTP 403/timeouts can legitimately remain pending. Require exact-source verification, stable IDs/receipts and ACK only after successful processing and durable upload. Compare preserved historical ACKs/receipts and newest monitor checkpoint, and verify browser-only feed behavior. Do not enable external channels or manually replay events.

## Safe rollback

Review branch `codex/production-baseline-rollback` starts from the same exact `116d78d` source. Its only runtime change removes candidate pruning. It preserves original scheduling and rearming, with the same effective 120/keep-vars configuration, no fairness cursor/claim changes and no migration. Tests and hashes are recorded independently.

For a regression during an approved rollout, preserve logs and the newest durable monitor artifact. Under the rollback authorization agreed for that rollout, verify the pinned rollback commit/hash/config and repeat its tests/dry-run; deploy that history-preserving rollback configuration with the same keep-vars and explicit 120 options. Record the resulting version and recheck bindings, history, alarms and normal monitor processing. Never restore old D1 snapshots, reset IDs/ACKs/receipts, rewind Actions artifacts or drop schema. Do not directly restore the original Cloudflare version: it would re-enable history pruning.

Stage B, migration 0005, SEC ingestion, new notification channels and changes to the 120-second interval remain unauthorized.
