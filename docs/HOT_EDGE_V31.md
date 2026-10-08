# Hot Edge v3.1 reliability contract

## Durable states

`0003_hot_edge_reliability.sql` expands D1; no old field/table is removed. Existing
SEC rows are returned to pending analysis, including the previously skipped
fourth/later filings. Legacy `enrichment_queued_at` is copied to dispatch time,
but never to ACK time. Wrangler's migration ledger applies the expansion once.

SEC discovery persists every matched accession before document I/O. Each alarm
analyzes at most three oldest eligible pending/failed documents in parallel. A
failed EX-99 gets exponential retry from one minute, capped at one hour; there is
no terminal retry cutoff. Complete documents retain a source hash and are skipped.
The feed discovery operations run independently; one failed feed cannot stop the
others. Alarms are scheduled from the start of the cycle; slow cycles still delay
their next invocation, so the 15-second interval is not a latency guarantee.

Materiality and polarity are independent. A material unknown-direction result is
eligible for Stage-0; it does not change Python's alert-priority/validation policy.

## Enrichment is at-least-once, with durable ACK

GitHub `queue: max` retains up to 100 pending runs in the single monitor lineage:
<https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency>.
The application does not rely on that queue for correctness. D1 retains every
material event until ACK. At most three unacknowledged dispatches are active;
additional events stay in D1. Dispatch HTTP 204 records the first dispatch time
and an attempt, never successful completion. If ACK is absent for 15 minutes
(`EDGE_ACK_TIMEOUT_SECONDS`, bounded 5 minutes–24 hours), dispatch is retryable.
HTTP/API failure uses exponential backoff capped at one hour, without discarding
the event. Cancellation, queue overflow, or a workflow failure cannot ACK a row.

The workflow passes `edge_event_id`, ticker, CIK, accession, and source URL. Python
retrieves the canonical event through authenticated `GET /edge/event?id=...`,
checks source hosts/SEC accession/CIK and the current issuer universe, reads the
exact source, and persists source evidence, CHG, outbox decision, and
`edge_event_links` in SQLite. The single monitor artifact is uploaded before
`python -m mozes.edge_enrichment ack`. The authenticated `POST /edge/ack` stores
CHG, GitHub run, alert IDs, and safe delivery statuses. Duplicate matching ACK is
idempotent; a conflicting CHG is rejected. Stable CHG/ALT IDs and story dedup stay
unchanged. A retry restores the artifact and reuses the durable receipt.

Every new monitor artifact includes `edge-checkpoint.json` with its producer run
and SHA-256 of the database. Restore admits cancelled/failed first-party main
runs only if that uploaded checkpoint matches the actual DB and run. This keeps
an ACK durable even when cancellation happens after upload/ACK but before the
workflow concludes. Unverified failed artifacts remain ineligible, and the
existing no-rewind and SQLite integrity checks still apply.

ACK proves durable enrichment/outbox disposition. It does not imply successful
phone/email delivery: pending, failed, filtered/no-alert, and sent remain distinct.
An ACK's delivery snapshot is the state at durable completion, not a live channel
receipt. Source documents, CHG records, and research/paper data remain append-only.

`EDGE_SYNC_TOKEN` is reused only for issuer sync, enrichment ACK, and private
event trace. It is never sent to the browser. Public `/edge/health` and
`/edge/metrics` contain counts/timings/source status, not event IDs, delivery
addresses, headers, or private provider errors. ACK is bounded to 16 KiB; private
trace returns one event. Use the private trace endpoint to inspect the run→CHG→ALT
chain; zero alert IDs means a durable filtered disposition, not a missing trace.

## Metrics and sources

Persisted cohorts by `first_seen_at` cover the latest 1h/24h, bounded to the most
recent 2,000 rows (truncation is explicit). Detection measures SEC acceptance or
wire publication to first-seen; separate metrics measure Stage-0 send, first
enrichment dispatch, and ACK. n/mean use valid non-negative elapsed times; p50,
p95, p99 use linear interpolation and are null with fewer than two samples.
Missing timestamps are never zero. Startup discovery of older feed items can
produce large latencies and remains included in the observational metrics.

The system-health UI distinguishes Edge, Stage-0 delivery, ACK, and existing
Python timings. It reads safe live Worker health with a persisted snapshot as
initial data. Source failures are explicit; overall status is PARTIAL when some
operations fail, FAILED when all fail, and UNKNOWN before the first check.

GlobeNewswire uses its verified RSS hostname, explicit non-empty User-Agent and
Accept, and an eight-second timeout. Offline parsing does not establish live
reachability. If Cloudflare still receives HTTP 520, health stays FAILED. The
existing Python GlobeNewswire feeds remain the reconciliation fallback; this is
not represented as fast Edge coverage. NTFY remains disabled by the owner's
choice until a destination is configured.

Targets are observational: SEC/wire source→Edge p50 <30s and p95 <60s. Do not
declare them achieved without production cohorts showing those values.

## Release and rollback

1. Run full Python/Node/CI Docker checks; review the diff.
2. Apply `npx wrangler d1 migrations apply mozes-alerts --remote` from `cloudflare/`.
   Verify historical rows remain present and ACK is not inferred from dispatch.
3. Merge the workflow/Python code to main before enabling the new dispatch shape.
4. Deploy `npx wrangler deploy`; existing sync/GitHub/SEC secrets remain in place.
5. Verify public health, issuer/event counts, source status, and next alarm. Track
   real event dispatches until private ACK has run/CHG/ALT and retry backlog drains.

For an emergency pause, deploy the same compatible Worker with
`npx wrangler deploy --var EDGE_ENRICHMENT_ENABLED:0`; detection/analysis/D1 remain
available, but new enrichment dispatch is paused. Resume with the configured
value `1`. Existing queued workflows may still finish and ACK. Preserve D1 rows
and SQLite artifacts. A same-Durable-Object-migration Worker rollback is supported;
the pre-v3 Worker cannot be directly rolled back across the `hot-edge-v3` DO
migration. Rolling back to v3 also removes ACK recovery behavior, so prefer the
pause switch while preparing a compatible repair. Do not drop migration columns.
