# Prospective evidence pilot — prepared code, inactive production

This increment is stacked on PR50 (`3b98dda63e1299ac64e27bc0fe7ea54e2b67e8e7`),
from production main `828245bab4981dd66e0597d2d0d653af74f0f247`. It does not require
PR48, PR49 or A5. No production schema, variable, scan or deployment was changed.
The baseline library contract is in `PROSPECTIVE_EVIDENCE_CAPTURE.md`; this document
adds its first optional monitor caller and the explicit preparation procedure.

## Review disposition and integration

PR50's schema/savepoints, immutable source references, current-time boundary, retry
identity, non-authoritative status and isolation from public signals were reviewed.
A synthetic metadata-corruption regression failed: changing the SQL observation key
was not detected by the reader. `_checked` now binds the row key, deterministic
capture ID, schema and reconstructed original request hash to the frozen payload.
Existing valid payloads/IDs are unchanged. This is corruption detection, not protection
against an administrator rewriting the schema, every row and every hash.

The only observer hook runs in `pipeline_v2c.main` after its monitor returns (including
ordinary source processing and alert queueing), before export/checkpoint. It uses the
same connection/DB and existing `mozes-hot-monitor` serialization. It neither creates
its own writer/connection/lock/workflow nor fetches anything. Export/Pages consumers
do not call it. Missing/exactly-off flag skips even importing the caller. An opt-in
observer exception cannot change the monitor result, prevent export/checkpoint, or
make an unrelated export failure successful. A failed/partial source monitor is skipped.

Only up to three explicitly allowlisted **live event IDs** are read and scored using
the existing current-day engine, without writing a score or changing its policies.
The full analysis freezes model versions, classification/selection decisions, features,
gate state and source metadata. A forecast window, when available, is copied from
that analysis. Its issuer/inferred origin and original creation/availability times
remain unknown: source bindings have not been independently established. Terminal
events are skipped. No paper signal, outbox entry, ACK, outcome or investment gate
is created. Date-only model output is not an intraday executable decision.

`event_sources.statement` is archived under a separate immutable deterministic
`PROSPECTIVE-SOURCE-` identity, with exact text, upstream identity/type and publication
time. It is explicitly an **observed excerpt**, not a complete HTTP response or proof
of historical availability. Original claimed retrieval time remains in metadata;
archive retrieval is the actual local observation time. Existing source/archive rows
are not replaced. Missing excerpts are reported as `source_gaps` and private capture
status `INCOMPLETE`; captured evidence remains durable and ordinary monitoring,
publication and alert status is unchanged. Missing/future
timestamps remain unknown/refused under the library rules. Unsafe references are
refused, including when an excerpt is absent. No API credentials/URLs are fetched.

One observation key per workflow run/event makes retries return the original capture.
New runs capture substantive analysis/source/rule changes, or an unchanged heartbeat
after six hours. Volatile observation clocks alone do not cause a new record. Multiple
revisions within a run, changes between scans, changes outside the allowlist, partial
source scans and budget-exhausted revisions are **not complete coverage**. Coverage
cannot be retrospectively repaired by backdating or replaying this mechanism.

META-M8 is still unresolved in production. Current market/model fields are unverified
analysis, not a selected immutable price series. `price_reference` stays unknown;
no PR48 staging activation, provider import, return reprocessing or performance claim.
All records remain `eligible=false`, `executable=false`, `authoritative=false`.

## Bounds and health

| Resource | Fixed pilot bound | Failure behavior |
| --- | --- | --- |
| Allowlist | 1–3 unique event IDs, required | Reject invalid configuration |
| New captures | 24 per UTC day, across all events/runs | INCOMPLETE, explicit skipped count |
| Unchanged heartbeat | Six hours per event | Report unchanged; no new row |
| Per-event inputs | 128 KiB event JSON; 32 source rows; 64 KiB combined excerpts | Refuse event, retain previous captures |
| Price context | At most 1,000 legacy rows per stock and XBI | Refuse event, no alternate prices |
| Observer work | Five-second **cooperative** budget; SQLite busy timeout 50 ms | Stop between bounded operations; not a preemptive CPU/filesystem deadline |
| Evidence ledger | 10,000 records, 64 MiB total UTF-8 payload, 256 KiB each | Refuse new captures; never trim/delete |
| New owned source archives | 16 MiB total content + metadata UTF-8 bytes | Refuse new archives |

The first reached budget wins. At 24/day the count-only ceiling is about 416 days;
real byte limits may arrive much earlier. The 80 MiB logical ledger/archive allowance
excludes SQLite/index/identity/URL/page overhead and existing DB growth. Measure actual
DB, compressed artifact size and restore/upload durations during the pilot; this is
not a filesystem quota. A prior local checkpoint sample was already ~263 MB, so the
workflow deliberately does not upload a second full DB backup.

Archive inserts may survive a later capture failure. They are durable observed source
evidence, not successful forecasts; they count toward the same 16 MiB bound. A ledger
at its count/byte ceiling stops before any more archives. Counters and safe error class
(no raw exception/source content) are private in `v2c_state.prospective_pilot_health`
and `.monitor/prospective-pilot.json`, included in the existing restricted monitor
artifact. Pilot health is not public pipeline/alert health. `health_persistence_failed`
means observability itself failed; never infer success from a green monitor alone.
The parent child-result includes best-effort capture status without changing its own
runtime status. The private DB remains authoritative if the small sidecar is absent.

## Explicit preparation — separate production authorization required

1. Review/merge PR50 and then this stacked PR under separate authorization. A merge
   starts ordinary CI/possibly Pages but leaves capture and preparation disabled.
   Recheck authoritative main, CI, normal monitor/Pages and checkpoint lineage first.
2. Select 1–3 verified live IDs and retain their inclusion rationale **before outcomes**.
   Verify their sources/forecast interpretation manually. Record main SHA and budgets.
3. Independently download the **latest authoritative existing** `mozes-live-monitor`
   artifact, verify its producer run/source SHA, manifest, `edge-checkpoint.json` and
   DB SHA-256. Retain it securely beyond GitHub's current three-day retention. In a
   scratch directory open its DB read-only, run SQLite integrity/FK checks, inventory
   histories/IDs/ACKs/receipts/gates/frozen attachment hashes, and prove a backup copy
   reopens with the same hash/inventory. Do not modify or restore it into production.
4. Under explicit DDL authorization, keep repository `MOZES_PROSPECTIVE_CAPTURE`
   absent/`0`. Set only the separately approved `MOZES_PROSPECTIVE_PREPARE` JSON to
   `{"source_run_id":"<retained producer run>","db_sha256":"<verified SHA-256>"}`.
   Wait for an **ordinary scheduled** serialized monitor. Do not dispatch/pause/cancel
   one. The optional preparation step runs immediately after restore, before writers.
5. This one process gets capture flag `1` solely to call the gated initializer; the
   job's observer remains off. It requires the ordinary main workflow identity, no
   incident recovery/source override, a standalone non-WAL existing DB, exact retained
   backup hash and valid integrity. It creates only the additive private capture
   table/index/immutability triggers and a preparation marker, atomically. It never
   adopts an unexplained existing capture table. Repeat preparation verifies the
   prepared schema without altering it. No other table/schema is migrated.
6. **The code does not independently verify remote backup availability or that its
   proof's run ID is the downloaded producer.** Those are operator checks in step 3;
   SHA equality binds the restored bytes. If another normal run advances the DB before
   preparation, hash mismatch refuses DDL safely; obtain/verify the new authoritative
   backup before another separately authorized attempt. Do not bypass the hash/lock.
7. Verify preparation success and normal checkpoint/upload from that run. Download
   that new artifact and run locally, against its scratch DB:
   `python -m mozes.prospective_pilot verify --database <scratch-db>`.
   Verify the preparation marker's retained-backup proof and that all previous durable
   rows/identifiers/attachment hashes and disabled gates are preserved. Clear only the
   preparation variable. Its value persisting would suppress capture intentionally.

## One ordinary run pilot — separate activation authorization required

1. Authorize repository `MOZES_PROSPECTIVE_EVENT_IDS='["<verified-live-ID>"]'` (start
   with one), then `MOZES_PROSPECTIVE_CAPTURE=1`; preparation must be clear. No B1,
   M8, A5, Stage-0, notification, schedule, Worker or gate configuration changes.
2. Wait for the next ordinary scheduled main monitor. Record run ID/SHA and source
   scan status. A partial/failed scan or skipped/incomplete observer is not successful
   prospective collection. First-run acceptance requires at least one sourced capture,
   no source gaps/errors, private health persisted, budgets satisfied and gates off.
3. Download the successful uploaded authoritative checkpoint. Verify DB SHA matches
   the run's checkpoint, preparation proof persists and each capture's run/event/key,
   request/payload hash, model/code/rule versions, forecast window/unknown metadata,
   actual captured UTC, source excerpt SHA/URLs/publication/retrieval and previous-link
   chain are consistent. Run the read-only verifier above. It checks SQLite integrity,
   triggers, bounded whole ledger, request/payload metadata, chain and source content.
4. Make a scratch copy, reopen it, and use `prospective_capture.as_of` to prove the
   same frozen records survive restore. Compare the pre-pilot durable inventory: no
   deletions/rewrites of immutable historical rows, IDs, attachments, ACKs or receipts.
   Ordinary real source activity may add legitimate alerts: reconcile those changes
   to their primary-source IDs; prospective IDs must never enter any public payload,
   outbox, paper signal or delivery receipt. Verify normal Pages publish/ACK/lineage.
5. Observe an ordinary later retry/tick for no duplicate, and an actual subsequent
   revision/heartbeat when available. Never invent a production revision to test it.
   The offline tests cover those paths; one run alone does not prove long-term capture.
6. Initially disable capture after the approved observation window unless sustained
   collection was explicitly authorized. Review real growth/runtime/source coverage
   before extending duration/allowlist. Do not increase fixed limits opportunistically.

## Rollback and stopping conditions

Disable `MOZES_PROSPECTIVE_CAPTURE` (`0`/remove), clear preparation, and let the existing
serialized run finish. Keep the latest authoritative checkpoint, preparation marker,
archives and ledger; reads remain available while disabled. Old code ignores this
additive private table. Do not drop data, restore the pre-pilot DB over newer alerts/
ACKs, rewind lineage or replay anything. Backup is an integrity/last-resort reference,
not authorization for destructive recovery. A broken checkpoint, missing history,
unexpected public disclosure, gate change or prospective notification requires HOLD
and separate incident investigation. Ordinary `producer_busy` still follows the
existing monitor/Pages exclusion/recovery flow.

GO for inactive code review only after exact-head CI. Production schema preparation
and collection remain HOLD until separately authorized steps above are satisfied.
PR49 is independent and ready for a separately authorized controlled merge on its
unchanged green exact HEAD. PR35 stays HOLD; PR46 remains excluded. No claim of alpha,
validated strategy, complete historical knowledge or executable investment eligibility.
