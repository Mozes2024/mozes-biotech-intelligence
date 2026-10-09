# Post-recovery audit — 2026-10-09 UTC / October 10 Israel time

Recovery producer 37993019164 and four automatic follow-ups uploaded full durable
checkpoints. Producer 37995108096 subsequently completed monitoring and uploaded
the latest verified checkpoint (SHA256
`a5dc4e3cc013524fa141df5986fac3b3b76a091145318a9a765bbd1a3720dba9`).
SQLite integrity is OK. All 1,039 incident-baseline changes, 37 outbox rows and six
historical Edge links are unchanged. TENX retains one log-channel receipt and its
original completion ACK. No external channels are enabled; do not activate them
as part of this repair.

## Website and scheduling evidence

Live Pages data generated at 21:46:11 UTC follows monitor manifest 21:45:17 UTC,
alert feed 21:45:43 UTC and successful Pages run 37995380953, completed 21:46:36 UTC.
The previous one-generation lag was the existing two-minute publication throttle,
not evidence of a corrupted export. CDN responses advertise max-age=600; normal
and cache-busting reads agree on the new generation. The feed refreshes independently.

Cron configuration remains unchanged: minutes 7,22,37,52, plus existing bounded
reconciliation slots. Workflow state is active, default branch is main, repository
is not archived/disabled/a fork. The latest observed cron run is 37975699213 at
18:47:17 UTC; it failed before recovery. No post-recovery cron execution was
observed during this audit. GitHub documents that schedules can be delayed or
dropped under load. That is a possible platform explanation, not a proven cause.
Do not increase frequency, disable gates, or call manual runs cron evidence.
Require a real `event=schedule` completion before declaring cron healthy.

Two automatic batch runs (37995108068 and 37995108436) failed restore while siblings
waited for the serialized lock. GitHub reports these waiters as `pending`, whereas
the audit previously ignored only `queued`. The fix accepts either waiting status
only after checking trusted metadata, attempt=1 and complete job evidence showing
no started jobs/steps. Active, completed-but-queued, rerun, untrusted and incomplete
evidence still blocks recovery. Do not restore an older artifact after a gate failure.

## Six pending events

| Event | Evidence / status |
| --- | --- |
| GKOS EDGE-3d3a4fb4e479a3864a65e58b | Latest attempt blocked by false pending-producer gate; primary-source completion not established |
| GKOS EDGE-ad40ba0f5fd5b6363342b9ac | A related wire change exists; canonical Edge enrichment/ACK remains pending |
| VIR EDGE-3a1f8b2daff5876b5fe398c3 | Exact Business Wire document timed out; a later restore also hit the pending gate |
| ALMS EDGE-0355d6c1d3230e9770d460fa | Exact GlobeNewswire document returned HTTP 403; no completion ACK |
| SGMT EDGE-7c12c33cf21cf04ab3d56122 | Exact document timed out; no completion ACK |
| ATOS EDGE-d221aba82c9b4438bffd21e4 | Known retained Edge filing, not captured in the verified monitor checkpoint; no successful canonical enrichment observed |

Public health reports 27 records, six pending material enrichments and zero pending
analyses. Private per-event state cannot be refreshed locally without the Actions
secret; this table distinguishes logs/retained evidence from fresh private traces.

The proposed transport performs at most two 20-second exact-source fetches for
timeouts or 500/502/503/504. Short valid Retry-After delays are honored; longer delays
stop immediate retries. 403/429 are never immediately retried. Existing durable
monitor observations retain one-hour denial cooldowns (or longer Retry-After) and
15-minute transport cooldowns across runs. Redirect host/HTTPS validation and byte
limits remain. Failed fetch/backoff never creates an event link or completion ACK.
No browser challenge bypass, alternate unverified publisher or synthetic source is used.

## Read-only SEC inventory

The frozen verified universe contains 310 issuers. All were scanned in passes of
at most three, at no more than one request/second. Recent submissions cover both
boundary days; no archive pages were needed. The tool allows at most two relevant
archives per issuer and stops globally on 403/429. Sixteen 8-K/6-K filings were found
on October 8–9. Ten are not represented in the monitor checkpoint. Separate official
submissions reads confirmed all ten acceptance timestamps fall between October 8
13:00 UTC (overlap) and October 9 21:26:14 UTC (first resumed scan).

| Ticker | Form | Missing monitor accession |
| --- | --- | --- |
| ICLR | 6-K | 0001628280-26-065404 |
| JAZZ | 8-K | 0001628280-26-065497 |
| PCRX | 8-K | 0001104659-26-114571 |
| VTGN | 8-K | 0001628280-26-065559 |
| ATOS | 8-K | 0001193125-26-418560 |
| BRTX | 8-K | 0001213900-26-108467 |
| EPRX | 6-K | 0001171843-26-006551 |
| PCVX | 8-K | 0001193125-26-419174 |
| CMND | 6-K | 0001213900-26-107727 |
| NSRX | 6-K | 0001493152-26-046340 |

This is complete inventory for the frozen universe/window, not complete ingestion,
materiality assessment or proof that all ten are absent from Edge. ATOS is already
known in Edge. Other Edge presence needs authenticated read-only trace evidence.
No missing filing was ingested, enqueued, published or acknowledged by this audit.
Raw reports, universe hash, checkpoints and logs remain protected outside Git.

Reproduce against a protected read-only checkpoint (never the production DB):

```powershell
python scripts/audit_sec_coverage.py --database <protected-checkpoint.db> --output <new-protected-report.json> --start 2026-10-08 --end 2026-10-09
```

Use an existing securely configured SEC identity; never print it. Output must be
new and separate from the checkpoint. Inclusive filing dates deliberately form a
conservative superset; confirm acceptance timestamps before approving ingestion.

## Review and controlled follow-up

Review/merge this focused repair separately. No production monitor run is dispatched
by the PR or audit. After approval, observe ordinary automatic restores against the
newest artifact, source cooldown preservation and ACK-after-upload behavior. Do not
manually replay pending events without approval. Review missing accession source
documents/materiality and durable Edge/change/delivery evidence before separately
authorizing bounded ingestion. Require cron completion and resolved coverage gaps
before Stage B. Keep EDGE_INTERVAL_SECONDS=120; no migration/deployment is included.
