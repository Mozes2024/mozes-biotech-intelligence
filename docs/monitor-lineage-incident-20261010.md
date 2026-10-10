# October 10 monitor lineage incident: reviewed recovery preparation

This is independent of the deployed D1 differential-sync fix and the consumed
TENX recovery authorization. No production recovery is authorized by this file.
The Worker remains `11c60e3e-b948-43db-90af-35e1e86bb56e`, with the approved
`13e487d7b85e45c6fc48efa70ce755ca91645c6fe35f6f213feeb5f8d94c01e3`
bundle and 120-second interval. No Worker, schema, interval or notification
configuration changes are part of this PR.

## Trusted immutable source

| Evidence | Verified value |
| --- | --- |
| Producer | [38052910145](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38052910145), successful schedule, attempt 1 |
| Producer code | `0a2874fb44b63fa4170df857d9e01b68e9e03884` on main |
| Artifact | `mozes-live-monitor`, ID `11669812295`, available |
| Artifact creation / expiry | October 10 12:43:26 / October 13 12:43:22 UTC |
| ZIP SHA256, matching GitHub digest | `8157f00682d5c415f6fca51e7d6341b714cd9d52fdf5da80b51be6e480f6c447` |
| Internal SQLite SHA256 | `0ca9e7f01ccb639914820c96489192d8c5e7842d098e52de0c7fcbb6c19687fb` |
| Checkpoint / manifest timestamp | October 10 12:43:21 UTC; producer ID and schema 1 match |

The full archive, downloaded files, API evidence and disposable rehearsal are
stored outside Git in the protected `lineage-incident-20261010-1620` directory
under the existing approved recovery backup. ACL permits only Moshe and SYSTEM.
No database, feed payload, source archive or credential is committed to Git.

At the read-only local audit, SQLite integrity and foreign keys pass. There are
1,099 change records, 106 source archives, 40 outbox records (38 sent/log, two
dead/log), 38 successful delivery records, eight Edge links and eight live
completed ACKs. All historical IDs/receipts/archives/links from the prior trusted
checkpoint are retained. The 40 published browser-feed changes and their sent
receipts are present. No pending/sending/failed outbox row requires provider
reconciliation. Browser delivery is the only configured user delivery experience.

There are 27 live Edge records, five material events pending, and no new Edge
record discovered after this checkpoint. Do not infer complete primary-source
coverage from an unchanged Edge count while production writes are constrained.

## Producer and gap evidence

Complete bounded metadata discovery read 1,585 monitor runs over 16 pages, with
a short final page proving discovery termination. Selection uses update/completion
time and all unfinished runs, including producers dispatched before the checkpoint.
The only later producer is
[38061061883](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38061061883),
attempt 1, scheduled at 14:48:13 UTC. Restore failed at 14:48:27; issuer sync,
enrichment, clinical processing, monitoring, artifact upload, ACK and feed/Pages
publication were skipped. There are no later active, queued, pending, cancelled
or rerun producers in that snapshot. The source producer's post-upload ACK/feed
steps completed successfully and agree with the live receipts.

No monitor execution was created between 12:41:54 and 14:48:13 UTC (126m19s).
The latter job started three seconds after creation, so runner/concurrency waiting
does not explain that recorded gap. The configured schedule remains
`7,22,37,52 * * * *` plus existing wider passes. The immediate failure mechanism is
the checkpoint's age exceeding 45 minutes, despite its availability and integrity.
It is not an artifact expiry, corrupt database, failed upload or newer lost state.

GitHub documents that scheduled events can be delayed or dropped under load:
[schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
This is consistent with the missing executions, but repository API evidence does
not prove the provider's internal reason. Do not claim a confirmed GitHub outage
or a specific D1 error from generic masked Edge source errors.

## Forward recovery plan: requires new explicit approval

The existing restore implementation already supports an independently pinned
checkpoint. MAX_REWIND and every history/producer gate remain unchanged. This
PR only repins the read-only verifier and permits its isolated incident branch
for pre-merge verification. It has read-only contents/Actions permissions, shares
`mozes-hot-monitor`, and contains no sync, enrichment, dispatch, upload, ACK,
delivery, source scan or publication step. It uses the existing Actions secret;
no local Edge token is requested or retrieved. Missing configuration fails closed.

Read-only verification, authorized separately from processing:

```sh
gh workflow run verify-monitor-recovery.yml \
  --repo Mozes2024/mozes-biotech-intelligence \
  --ref codex/lineage-incident-20261010
```

Require actual successful `VERIFIED_NO_WRITES` from that run. TENX_ELIGIBLE is
intentionally irrelevant: TENX is already completed and its old authorization
was consumed. A local administrative SELECT snapshot also verifies receipt
continuity, but does not impersonate an authenticated Edge-interface check.

Before a separately approved production run:

1. Review/merge this verifier-only PR if desired. Recheck current main/code,
   source artifact ID/digest/internal checksum and newest producer discovery.
   A newer durable checkpoint invalidates these inputs immediately.
2. Repeat complete producer audit, live Edge completion/receipt comparisons,
   published feed reconciliation and absence of ambiguous deliveries, under the
   workflow lock. Stop on incomplete evidence or contradiction.
3. Confirm production D1 write capacity and compatible current environment.
   Read success and zero hourly writes while processing is blocked do not prove
   capacity. Do not upgrade billing or probe capacity by synthetic writes.
4. Obtain explicit approval for ONE new bounded, notification-producing monitor
   recovery using the following inputs. No forced Edge event or historical SEC
   ingestion is included; five source-blocked events remain automatic fair-queue
   work after capacity returns.

```sh
# PLAN ONLY — NOT EXECUTED; separate production approval required.
gh workflow run lightweight-monitor.yml \
  --repo Mozes2024/mozes-biotech-intelligence --ref main \
  -f priority_only=true -f source_run_id=38052910145 \
  -f recovery_checkpoint_sha256=0ca9e7f01ccb639914820c96489192d8c5e7842d098e52de0c7fcbb6c19687fb
```

5. Restore repeats the original gates before any processing/write and copies the
   complete database. Bound the first run to the priority pass; preserve original
   source publication/acceptance times and canonical source/accession identities.
   The existing HTTP 403/429/Retry-After and durable cooldown rules remain intact.
   Blocked sources produce no archive/change/link/completion ACK.
6. Require checksum-bound checkpoint creation/upload even after a downstream
   failure. Verify baseline records, existing per-channel receipts, feed revision,
   new records and any actual delivery/ACK against this first new artifact. ACKs
   require successful processing and upload. Preserve logs before any retry.
7. After a new artifact exists, never reuse this old source override. Observe
   normal scheduled processing from the newest checkpoint. Do not dispatch
   additional manual pending-event runs or reset attempts/receipts.

If newer undurable writes or ambiguous delivery appear, recovery is NO-GO:
preserve the newest available state and reconcile trusted producer/provider
records before another attempt. A code rollback must retain newest state; never
restore this incident database over a newer artifact or clear sent/dead receipts.
No Cloudflare rollback or database rollback is part of this procedure.

## Bounded primary-source reconciliation

Five existing unprocessed events are ALMS, VIR, GKOS (two), and SGMT, all without
completion ACKs. They are captured, not missing Edge records. Last durable source
evidence retains GlobeNewswire HTTP 403 for ALMS/SGMT and BusinessWire timeouts
for VIR/GKOS. Dispatch attempts/ACK timeouts are not successful processing.

A read-only pass fetched official SEC submissions for TENX, ATOS and GKOS, at
most one request per second. No new 8-K/6-K acceptance since 12:43:21 UTC was
found for those three issuers. Coverage of the other 307 issuers is not certified;
historical SEC gaps and PR #17/ATOS correction remain separate.

Freeze the 310-issuer snapshot and scan remaining issuers in batches of three,
one SEC request/second, stopping on 403/429 and honoring Retry-After. Inventory
8-K/6-K acceptances in `[12:43:21 UTC, approved recovery cutoff]`, with a small
overlap; compare accession observations, immutable changes, archives, outbox
IDs, published feed and Edge records. Follow a submissions archive only when
its declared date range overlaps the window. Preserve original accepted/filed
times and document per-issuer coverage. Report missing candidates separately;
materiality review and notification-producing ingestion require approval.

## Disposable-copy rehearsal and recurrence prevention

The existing restore was rehearsed twice against captured verified evidence:
both restores preserved the exact source DB SHA256. Re-enqueuing all 40 existing
alert records twice created zero rows; dispatch made zero sender calls. Repeated
processing of all eight linked events performed no fetch and preserved IDs.
Simulated publisher 403/timeouts for all five pending events created no change,
source archive, link, receipt or ACK; backoff persisted only in the disposable
copy. Original history/outbox/delivery/link rows remained identical. Integrity
and foreign-key checks passed. This is not a claim that blocked primary sources
were fetched successfully or that live write capacity recovered.

Propose a separate recurrence-prevention change: detect checkpoint age approaching
30 minutes and report source freshness independently of checkpoint durability.
Review a serialized, full-provenance stale-recovery path requiring newest-artifact,
complete producer and receipt proofs, rather than making ordinary restore rely
solely on a 45-minute wall clock. Do not increase MAX_REWIND, weaken gates or
increase cron frequency in this incident PR. GitHub schedule alone cannot provide
a hard maximum inter-run interval.
