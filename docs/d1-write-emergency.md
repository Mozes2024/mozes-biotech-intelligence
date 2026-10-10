# Isolated Stage A issuer-sync write incident

## Evidence (2026-10-10 UTC)

Read-only Cloudflare account GraphQL analytics reported 100,909 rows written at
12:07:42 and 104,651 at 12:15:45. Both account queries returned only database
`9baf99f2-1ccc-4415-affd-2708a7210e7b` (`mozes-alerts`); no other database
contribution appeared. Remaining allowance against 100,000/day is zero. The
analytics total is over the allowance, although health and automatic monitor
requests were still succeeding: quota enforcement must not be inferred from a
healthy public endpoint. Free allowances reset at 00:00 UTC, October 11.

Hourly writes: 07:00 7,541; 08:00 16,274; 09:00 12,533; 10:00 14,999;
11:00 14,987. Deployment occurred during the mixed 09:00 hour. The increase
predates fairness; this is not evidence that fairness introduced issuer writes.
The production bundle still matches approved Stage A `7197fb228615914b2a575955ecefe0abd671d28b`.

A bounded production COUNT read (310 rows read, zero written) confirmed 310/310
active issuers. Successful monitor logs, including run 38050092253 at
11:55:51 UTC, report issuer sync `OK`, count 310. The 11:00 hour contains twelve
successful automatic monitor runs; every run executes `mozes.edge_sync`.
The exact deployed SQL rewrites all 310 twice, including the active index.
Actual isolated workerd metadata measures **1,240 rows written per unchanged
sync**. Twelve calls explain 14,880/14,987 writes (99.3%) in the 11:00 hour.
This attribution combines measured account metrics, successful sync logs and
the exact SQL/local metadata; request-level production D1 tracing was not
available and no diagnostic code was deployed.

Other runtime writes remain: candidate discovery/reclassification/decisions,
event insertion/classification, enrichment claims/dispatch bookkeeping, ACKs
and alert-feed publication. They are unchanged. They account for the residual
hourly writes in this model, rather than being independently metered per path.

At approximately 15,000/hour, 7,000 remaining writes would last about 28
minutes. Current analytics already exceed the allowance; there is no positive
estimated runway. A tested correction prevents future waste but cannot refund
today's writes or guarantee service before reset. Recommend a separately
approved temporary Workers Paid upgrade if continuity is needed before reset;
do not change billing automatically. It preserves coverage, frequency and
verification. Waiting for reset risks missed discovery and delayed ACK/feed
publication; no disabling monitoring or source verification is recommended.

## Minimal fix and concurrency semantics

Only `cloudflare/src/index.js::syncIssuers` changes at runtime. Authentication,
body limits and complete-universe validation remain; confidence must be numeric
and CIK aliases are canonicalized before duplicate detection. A bounded active
snapshot comparison returns success with zero writes and preserves timestamps
when identical. This read is the no-op request's linearization point.

Changed requests use a single atomic D1 batch: read the removal gate, deactivate
only removed identities, then conditionally upsert changed/new/reactivated
identities. The authoritative snapshot is evaluated inside the transaction,
not planned from the earlier read. Concurrent changed snapshots serialize as
complete snapshots; the last accepted transaction wins. There is no timestamp
ordering protocol added, and existing GitHub monitor concurrency remains
essential to producer ordering. Every retained row and source field survives.

Reject removal of more than 5% of currently active identities (allow one for
small universes), including replacement universes disguised by additions.
Empty/invalid/duplicate requests fail before writes; suspicious snapshots return
409 with no writes. Legitimate large universe changes require explicit review,
not a bypass or forced sync. A syntactically valid omission within this bound
cannot be distinguished from an authoritative removal by the existing protocol.
No new schema, cache, Durable Object state, binding or migration is introduced.

Scheduler, alarms, fairness, retries, ACK-after-upload, retention and browser
delivery remain the production implementation. PR #17 and main's Stage B Worker
are not included. This PR targets `codex/production-baseline-fairness` to keep
the release isolated; it must not be used to replace main's Stage B code.

## Verification and budget

Complete production-baseline Python suite: 472 passed (`python -m pytest -q`).
Complete Worker suite, syntax checks and pinned Wrangler 4.149.0 dry-run pass.
CI additionally tests the actual container build using the already reviewed
Docker Official Python ECR digest from PR #14, avoiding the old Docker Hub tag.
The Miniflare/Wrangler development dependencies are pinned; they do not enter
the production bundle.

`npm test --prefix cloudflare` includes real isolated D1 migrations 0001–0004:

| Case (310 issuers) | Rows read | Rows written |
| --- | ---: | ---: |
| Initial full snapshot | 621 | 930 |
| 100 identical snapshots total | 31,000 | 0 |
| One changed issuer | 4,961 | 2 |
| One new issuer | 4,967 | 3 |
| One legitimate removal | 4,969 | 2 |
| Current production identical snapshot | 620 | 1,240 |

Tests cover canonical duplicate CIKs, malformed JSON/records, unauthorized
requests, empty/shrunken/replacement snapshots, partial-batch failure rollback,
racing snapshots, concurrent identical retries, unchanged timestamps,
eligibility and absence of synthetic events/ACKs. Existing regressions preserve
original overlapping alarms and start-based rearming, cursor persistence,
three-active claims, source deadlines, historical records and migration list.

At twelve unchanged syncs/hour: save 14,880 writes/hour or 357,120/day,
and reduce stable sync reads from 178,560 to 89,280/day. The observed other-write
residual (107–119/hour) projects approximately 2,568–2,856/day, leaving roughly
97,144–97,432/day margin after reset under this workload. These are projections,
not guarantees: discoveries, changed universes and backlog activity add writes.
Changed-snapshot guards cost about 5,000 reads per call at this size; frequent
actual churn requires remeasurement. No unchanged-call scan amplification.

## Immutable build and protected recovery evidence

Candidate bundle SHA256:
`13e487d7b85e45c6fc48efa70ce755ca91645c6fe35f6f213feeb5f8d94c01e3`.
Use the exact reviewed PR head SHA, recorded separately in the release receipt.

Rollback commit: `7197fb228615914b2a575955ecefe0abd671d28b`.
Fresh rollback bundle SHA256:
`66dfaba39549511653d16d8292a738b7413fe750c42211a4e0ff2695e32df34b`.
It preserves fairness and history, but restores issuer write amplification;
rollback therefore does not solve quota exhaustion. Never use the historical
pruning-enabled Worker version.

Expected current Worker version: `d3867a64-2276-4b68-8f57-51594757ad8b`.
Fresh protected checkpoint from run 38051034861 has verified integrity and SHA256
`3f7723a8b7bdd46585e5a5d338afbc8600c1a41b0fa2702983df6957ac858703`:
1,099 changes, 40 receipts (38 log/sent, two log/dead), eight Edge links.
All prior 1,094 changes and 39 receipt identities/statuses/timestamps/attempts
remain. Production has 27 events, eight ACKs and five material pending events
(GKOS twice, VIR, ALMS, SGMT); candidates grew to 115 without history deletion.

Evidence, downloaded checkpoint, builds, environment snapshots and hashes are
outside Git in the protected local `d1-write-emergency-20261010` folder.
Never put the database, OAuth credentials or secret values in Git/CI output.

## Separately approved execution only

1. Require green CI and unchanged reviewed candidate SHA. Recheck Worker
   version/content hash, health, usage, checkpoint/receipt preservation and
   absence of another rollout. Stop on drift or failed gates.
2. Verify interval 120, cron `*/2 * * * *`, compatibility date 2026-08-01,
   `ALERTS_DB` database identity, `HOT_EDGE` namespace/class and migration tag
   `hot-edge-v3`, migrations 0001–0004, existing secret names and disabled
   external delivery. Preserve all effective production variables with keep-vars.
3. Build candidate and rollback again with Wrangler 4.149.0 and compare the
   exact hashes above. Runtime source diff must contain only issuer sync:
   `git diff 7197fb228615914b2a575955ecefe0abd671d28b <reviewed-SHA> -- cloudflare/src`.
4. After separate deployment approval, from the pinned isolated checkout run
   `cloudflare/node_modules/.bin/wrangler deploy --config cloudflare/wrangler.toml --keep-vars --var EDGE_INTERVAL_SECONDS:120` exactly once.
   Do not deploy main or apply any migrations.
5. Observe normal automatic syncs (no manual alert-producing dispatch): unchanged
   successes, unchanged active issuer identities/timestamps, healthy alarms,
   durable checkpoints, intact ACKs/receipts, browser feed/Pages and hourly writes.
   Analytics may lag; lowering future rate does not undo consumed allowance.
6. If integrity/eligibility/scheduling regresses, stop and obtain/execute the
   separately authorized rollback from pinned `7197fb2`, after hash/configuration
   checks. Use the candidate's verified Wrangler binary with the rollback's
   configuration and the same keep-vars/120 flags. No D1 restore/reset/deletion.

No production deployment, billing change or notification-producing manual run
was performed during investigation. Emergency technical GO requires green CI;
production execution and any paid-plan fallback require separate authorization.
