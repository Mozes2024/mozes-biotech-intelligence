# Controlled monitor lineage recovery

This operation repairs GitHub Actions only. It does not authorize a Cloudflare
deployment, migration 0005, changes to secrets/variables, or a monitor run that
sends notifications. Keep `EDGE_INTERVAL_SECONDS=120`.

## Evidence, 2026-10-09 UTC

[Run 37926270968](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/37926270968)
failed at restore; monitoring, enrichment, delivery and ACK steps were skipped.
The latest durable checkpoint is
[run 37786606409](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/37786606409),
artifact 11554757315, created 2026-10-08 13:45:37 UTC, retained and unexpired.
Its checkpoint/manifest time is 13:45:32 UTC and its database SHA256 is:

```text
f84633088fe4c6fe622682d852a925567118f371e8950f09e9ebaac7898ec853
```

SQLite integrity is `ok`. It contains 1,039 changes, 37 outbox rows (35 sent,
2 dead, no pending/sending/failed), 6 Edge links, 250 enqueue decisions and 89
source archives. A fresh read-only D1 audit and current public feed confirmed
all 6 completed Edge IDs/change IDs and all 37 published changes/delivery
receipts are represented. The full checkpoint, not a selected-table export,
preserves the other historical tables too.

The 12:50:18 UTC full-history audit covered 252 subsequent completions, including
runs dispatched before the checkpoint and completed afterward. Seven failed while
fetching a Business Wire document with `TimeoutError`, before archive/change/
outbox writes; other runs failed restore. Their enrichment implementation at
`116d78d9bce29ef94836a47753c656b59c462871` is identical to the implementation
at `3d69bf5`. No subsequent delivery/monitoring step ran. Evidence is a point-in-time
assessment; the recovery gate repeats it under the serialized workflow lock.

The cause was artifact-less failed attempts occupying the latest-20 run window
and the latest-12 restore candidates. They also advanced the reference time for
the 45-minute rewind cutoff. Repeated pre-write wire timeouts stopped checkpoint
production. This is **not** an expired artifact, invalid checksum or corrupt DB.
Failed producers with an uploaded, hash-bound checkpoint remain usable; failed
producers without one are not interchangeable with a successful checkpoint.

## Recovery gates

Discovery uses retained artifact producers, including expired artifact records
so loss of a newer checkpoint cannot authorize fallback. Failure to download or
validate the latest recognized checkpoint stops restoration. An existing local
database with different bytes is not overwritten. No empty DB is bootstrapped.

The 45-minute safeguard remains. Ordinary restore requires a recent checkpoint
and audits later producer attempts for possible writes. Checkpoint time indicates
durability; the unchanged manifest timestamp indicates source freshness. Neither
recovery nor partial-run checkpointing fabricates a successful source scan.

Stale recovery additionally requires an explicit producer ID and reviewed SHA256,
the latest recognized artifact, SQLite integrity, complete later-run/job evidence,
matching existing Edge completions, all published changes/sent receipts, and no
ambiguous nonterminal delivery rows. Reruns, active producers, missing logs/jobs,
unreviewed enrichment failures, or more than 500 relevant attempts block automated recovery. Stale recovery scans
up to 5,000 workflow-run metadata records, selecting by completion/update time
rather than dispatch time; this includes older queued runs and historical reruns.
Incomplete discovery also blocks recovery. Dispatch ordering is not assumed; see
[GitHub concurrency semantics](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
The audit reads Actions and Edge/feed records; it never manufactures sent receipts.

If newer processing occurred without a durable artifact, **stop**. Recover its
checkpoint, provider delivery receipts and independent durable change records
first. Build a separate candidate DB by unioning records under their existing
primary keys; verify full baseline retention, resolve every conflicting payload,
ACK and per-channel receipt explicitly, and account for every newer producer.
Require checksum/foreign-key/integrity checks and replay tests showing zero repeat
sends. Current source data may reconstruct missing evidence, but cannot prove
whether a notification was sent. Missing provider receipts leave recovery NO-GO.
This PR deliberately does not implement a speculative database merge or synthesize
history from headlines. For the audited incident, no such merge is necessary:
all committed history is in the full retained checkpoint; source reconciliation
adds new records forward from it.

## Controlled procedure — requires separate approvals

1. Review and approve merging the focused recovery PR after CI passes. Merging
   changes the scheduled Actions code; stale scheduled restores continue to fail
   closed without the explicit recovery checksum. Do not rerun old monitor runs.
2. Preserve the full artifact outside Git in a protected directory, along with
   its manifest, checkpoint, producer/job audit and D1/feed evidence. Verify the
   SHA256 above and isolated SQLite integrity/counts. Preserve existing backups.
3. Immediately before resumption, recheck that 37786606409 is still the latest
   durable producer and repeat the producer/D1/feed comparisons. A newer checkpoint
   invalidates this recovery recipe. Do not delete newer artifacts to make it pass.
   Read-only verification can be run in an authenticated, isolated checkout:

   ```sh
   # Supply EDGE_SYNC_TOKEN securely; never echo it. GH_TOKEN is read-authenticated.
   export GITHUB_REPOSITORY=Mozes2024/mozes-biotech-intelligence
   export MOZES_EDGE_SYNC_URL=https://mozes-hot-clock.sp500.workers.dev/edge/sync
   export MONITOR_RECOVERY_SHA256=f84633088fe4c6fe622682d852a925567118f371e8950f09e9ebaac7898ec853
   python scripts/restore_monitor_state.py --run-id 37786606409 --verify-recovery-only
   ```

   Require `VERIFIED_NO_WRITES`; this does not populate `.monitor`, send alerts,
   publish feeds, sync issuers or ACK events. GitHub secret values are not readable
   through `gh secret list`; an operator needs an authorized secure environment.
   Do not substitute an empty token or claim private endpoint verification passed.

   Prefer the isolated GitHub Actions verifier after its PR is approved and merged;
   it uses the existing repository secret without copying it to a local machine:

   ```sh
   gh workflow run verify-monitor-recovery.yml --repo Mozes2024/mozes-biotech-intelligence --ref main
   ```

   This manually triggered workflow has only `contents: read` and `actions: read`,
   fixed producer/checksum inputs, and no processing, sync, artifact upload, ACK,
   delivery or publishing steps. It shares the monitor concurrency lock, so the
   full-history/receipt snapshot cannot overlap an active monitor producer.
   Require successful completion and `VERIFIED_NO_WRITES` from the verification
   step; missing/unusable control configuration reports only `EDGE_SYNC_TOKEN`
   or `MOZES_EDGE_SYNC_URL`, without values. Connections close on both success
   and failure, and temporary cleanup cannot replace the original gate error.
   Capture the verification run URL and reviewed main SHA. Before the single
   authorized alert-producing dispatch, recheck the producer is still latest,
   no conflicting execution exists and every original safety condition holds.
   A verifier success does not authorize additional processing or checkpoint reuse.
4. Obtain explicit approval to resume notification-producing processing. Then
   execute **one new** manual main-branch run, prioritizing the older SEC backlog:

   ```sh
   # DO NOT execute until resumption is separately approved.
   gh workflow run lightweight-monitor.yml --repo Mozes2024/mozes-biotech-intelligence \
     --ref main -f priority_only=true -f source_run_id=37786606409 \
     -f recovery_checkpoint_sha256=f84633088fe4c6fe622682d852a925567118f371e8950f09e9ebaac7898ec853 \
     -f edge_event_id=EDGE-24534abeca26dcdd08c16db6
   ```

   Inputs are operator reviewed, not credentials. The workflow independently
   repeats the gates before copying the full checkpoint and records recovery proof
   in its artifact. Canonical source/issuer evidence is fetched using the existing
   Edge event ID. Failed enrichment is not ACKed. General reconciliation proceeds
   after a source failure; successful restoration always attempts checkpoint/upload,
   even if a later step fails. ACKs require both successful enrichment and upload.
5. Inspect that first run before any further manual recovery: verify uploaded DB
   checksum, retention of baseline changes/receipts/dedup, new event link/outbox
   decision, per-channel delivery state and D1 ACK. Subsequent runs use the **newest**
   artifact normally, with no stale source/checksum override. Never reuse the old
   recovery override after a new producer uploaded state. Review three completed
   monitor cycles and the decreasing backlog before accepting fallback coverage.
6. If processing fails after a send, preserve all uploaded artifacts/logs and pause
   further manual dispatches. If no checkpoint uploaded, stop automatic recovery;
   reconcile provider receipts before retry. Do not roll back the monitor DB to the
   incident checkpoint, clear sent/dead rows, delete event links or reset Edge attempts.
   A code rollback must keep the newest verified state. External delivery is
   at-least-once: provider acceptance followed by a crash before receipt persistence
   still needs explicit receipt reconciliation; this fix cannot promise exactly-once.

## Pending events and forward reconciliation

The original six pending material events became seven during diagnosis:

| Event ID | Source / ticker | SEC accession |
| --- | --- | --- |
| EDGE-3d3a4fb4e479a3864a65e58b | Business Wire / GKOS | — |
| EDGE-3a1f8b2daff5876b5fe398c3 | Business Wire / VIR | — |
| EDGE-24534abeca26dcdd08c16db6 | SEC / TENX | 0001193125-26-417939 |
| EDGE-0355d6c1d3230e9770d460fa | GlobeNewswire / ALMS | — |
| EDGE-7c12c33cf21cf04ab3d56122 | GlobeNewswire / SGMT | — |
| EDGE-ad40ba0f5fd5b6363342b9ac | Business Wire / GKOS | — |
| EDGE-d221aba82c9b4438bffd21e4 | SEC / ATOS | 0001193125-26-418560 |

All seven have completed material analysis, no completion ACK, no Stage-0 sent
timestamp, and no link in the incident checkpoint. Dispatch attempts are not
delivery receipts. After the first approved checkpoint, process ATOS and the wire
events one at a time using only canonical `edge_event_id` and no stale restore
override; wait for artifact/ACK evidence between manual runs. Automatic retries
remain active on the existing Worker and use the same serialized Actions lineage.
Do not bulk dispatch, reset attempts or bypass a failed wire fetch with synthetic
content. A wire timeout leaves that event pending without blocking other monitoring.

Existing enrichment keeps `EDGE` IDs, stable SEC accession/canonical-wire `CHG`
identities, primary corroboration links, enqueue policy decisions, per-channel
outbox IDs and sent receipts. Repeated enrichment/ACK retries reuse those records;
tests exercise failed fetch, successful retry, sent-state replay and two Edge IDs
for the same source with one delivery.

TENX and ATOS are known **unenriched**, not missing from D1. No claim of complete
SEC coverage is justified by a recovered latest-feed poll: feeds are finite and
Actions monitoring was unavailable after the 2026-10-08 13:45:32 checkpoint.
Bound reconciliation to that timestamp through the first healthy resumed run,
including an overlap starting 2026-10-08 13:00 UTC. Freeze the verified issuer/CIK
universe; compare per-issuer SEC submissions (8-K/6-K) and retained Edge records
against monitor accession observations and stable change IDs. Process at most
three issuers per pass, at most one SEC request/second, honor Retry-After, and stop
on 403/429 rather than repeatedly probing. Follow submissions archive references
only where their dates overlap the window; record cursor/boundary evidence and
unresolved issuers. Current latest-feed pages alone cannot certify the outage window.

First inventory missing accessions without dispatching or publishing. Review that
inventory before approved ingestion into the **newest** full lineage using existing
accession identity, source provenance/materiality rules and outbox dedup. Do not
use a separate sender or create synthetic Edge IDs/ACKs for filings absent from D1.
This bounded historical inventory and its notification-producing ingestion have
not been executed by this PR. Unresolved coverage blocks Stage B sign-off. The
Stage B backup/migration/rollback runbook remains [d1-optimization.md](d1-optimization.md).
