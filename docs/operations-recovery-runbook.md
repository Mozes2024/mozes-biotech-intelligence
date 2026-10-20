# BIO operations: preserve evidence before recovery

This is a diagnosis and approval guide, not authorization to activate recovery,
replay, migrate D1, deploy a Worker, change cron or enable notification channels.

## Read-only diagnosis

1. Inspect real producer and Pages runs, distinguishing `schedule` from Worker
   `workflow_dispatch`. Inspect failed-step logs, queue/start times and exact code SHA:
   `gh run list --repo Mozes2024/mozes-biotech-intelligence --workflow lightweight-monitor.yml --limit 20`
   and `gh run view RUN_ID --repo Mozes2024/mozes-biotech-intelligence --log`.
2. Record the newest authoritative artifact's producer, artifact ID, internal
   checkpoint SHA256, manifest and schema. Download evidence outside Git. Validate
   hash, database integrity, provenance, retained events and delivery receipts.
   An expired/missing newest artifact is a blocker, not permission to use an older one.
3. Compare the live `updates.json`, immutable Pages artifact, consumer checkpoint
   and post-deployment `mozes-published-ledger` receipt. Publication requests and
   successful source scans are not publication ACKs. Pages is a read-only consumer.
4. Read deployed Worker version, bindings and D1 schema before assessing code
   compatibility. Keep tokens and raw private evidence out of logs and commits.

Details: [monitor lineage](monitor-lineage-recovery.md),
[Pages coordination](pages-lineage-coordination.md),
[post-recovery audit](post-recovery-audit.md).

## Transient failure or a coverage limitation

`producer_busy`, a changing inventory or a temporary GitHub transport failure can
delay publication while durable state remains intact. Inspect automatic bounded
retry outcomes and the shared non-cancelling monitor/Pages queue; do not cancel its
owner, reset state or manually scan production to manufacture success. Missing,
ambiguous or conflicting provenance must remain fail-closed.

A publisher HTTP 403, bounded IR coverage, blocked oversized analysis, historical
manual-review work or disabled alert channels is not checkpoint loss. Preserve
honest FAILED/PARTIAL states. Do not bypass publisher access restrictions, discard
queue entries, enable channels or replay old events to make health green.

## Suspected state loss: stop before mutation

Preserve the current state and last verified durable artifact independently.
Establish the precise lost lineage and compare protected events, outbox rows,
delivery receipts, source archives and Edge links. Document exact producer IDs,
hashes, deployed version/schema, current head and proposed rollback boundary.
Without complete, consistent evidence, stop and report the concrete blocker.

An explicit human approval must name the exact recovery/deployment/migration
action and bounded execution count. A prior one-use approval is consumed once;
it does not authorize retry, history rewind, artificial ACKs, historical sends,
Deep Monitor, additional features or a database reset. Follow the existing
[incident procedure](monitor-incident-20261020.md); never infer authorization from it.

## Worker compatibility and forward-only preservation

The approved October 20 deployment was Stage A with migrations 0001–0004.
Main contains Stage B code requiring 0005: never deploy that entrypoint against a
Stage A database. Verify actual production schema/version anew before any future
approved deployment. See [Stage A stabilization](stage-a-stabilization.md) and
[D1 compatibility](d1-optimization.md); no remote migration is implicit.

Preserve the fixed `EDGE_FORWARD_ONLY_FROM` activation evidence across restarts.
Never move it backwards, clear delivery cursors/receipts or turn manual historical
work into new alerts. Verify authentic before/after ACKs, RAPP preservation, MESO
blockage and ordinary monitoring/publication. A new material event is required to
claim measured post-deployment enrichment latency; fixtures cannot satisfy it.

## Known limitations and generated data

At the final incident audit (October 20, 18:36 UTC): 30 sources FAILED, seven
PARTIAL, TYRA IR HTTP 403, 33 historical enrichment items for manual review,
`enabled_channels=[]`, and no new qualifying event completing enrichment after
deployment. Recheck evidence before updating these facts. Source access and
external notification setup are separate user-approved product work.

`web/data.json` is generated, ignored output, never a durable checkpoint. CI
bootstraps an isolated fixture and exports it; Pages restores the authoritative
database and runs consumer-only export before upload. For local static use run
`mozes export-v3` against the intended local database first. Missing local output
is a build prerequisite, not a reason to copy a stale snapshot into Git.
