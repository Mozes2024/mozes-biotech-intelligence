# Prospective evidence pilot — disabled-by-default, one-shot authorization

The one-shot safeguard is based on production main
`c38e85b770c65b1c190accd3ab84ca4f8a79e4c2`, following merged PR50/PR51.
It changes no production schema, variable, claim, scan or deployment during review.
It does not activate PR48 or A5. The global capture flag alone now fails closed.
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

The one-shot scope permits only **PHAR-SNDA-LOWER-WEIGHT**, read and scored using
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

The standalone immutable library preserves retry identities; the production monitor
now rejects reentry rather than retrying an authorized attempt. A distinct future
authorization requires fresh explicit approval. Its bounded observer still skips an
unchanged heartbeat within six hours. Volatile clocks alone do not cause a new record. Multiple
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
| Allowlist | Exactly `PHAR-SNDA-LOWER-WEIGHT` for this one-shot scope | Reject invalid configuration |
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

1. Review/merge the one-shot safeguard under separate authorization; the original
   library and monitor integration are already merged. A merge
   starts ordinary CI/possibly Pages but leaves capture and preparation disabled.
   Recheck authoritative main, CI, normal monitor/Pages and checkpoint lineage first.
2. Reverify `PHAR-SNDA-LOWER-WEIGHT` is live and retain its inclusion rationale
   **before outcomes**; no other event is authorized by this scope.
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

## One ordinary scheduled run — separate activation authorization required

1. Complete the backup/restore, live-event and normal Pages/ACK gates above, including
   recovery after any publication-preflight failure. Preparation must be clear and
   capture off. Obtain explicit approval for the exact **post-merge main SHA** and
   this single event/window. Do not use this PR's branch SHA as production approval.
2. Generate a new authorization ID offline: `YYYYMMDDTHHMMSSZ.<approved-SHA>.<32-hex-nonce>`.
   The UTC issue time is fixed; the ID is valid for one hour. Only a `schedule` run
   created in that interval may claim it. Generate the nonce with `uuid.uuid4().hex`.
   Set `MOZES_PROSPECTIVE_AUTHORIZATION` first, then
   `MOZES_PROSPECTIVE_EVENT_IDS='["PHAR-SNDA-LOWER-WEIGHT"]'`, then
   `MOZES_PROSPECTIVE_CAPTURE=1`. Never auto-generate or renew a production ID. No B1,
   M8, A5, Stage-0, notification, schedule, Worker or gate configuration changes.
3. Wait for the next ordinary scheduled main monitor. Edge/manual `workflow_dispatch`
   runs cannot claim this authorization. Do not dispatch a schedule or alter its cron.
   Record authorization ID, claim commit/ref, actual run ID/attempt and code SHA. A
   denied/failed claim, expiry, or ambiguous API response means HOLD: disable capture,
   retain the evidence and do not retry/reuse the authorization. Record source
   scan status. A partial/failed scan or skipped/incomplete observer is not successful
   prospective collection. First-run acceptance requires exactly one sourced capture,
   no source gaps/errors, private health persisted, budgets satisfied and gates off.
4. Download the successful uploaded authoritative checkpoint. Verify DB SHA matches
   the run's checkpoint, preparation proof persists and each capture's run/event/key,
   request/payload hash, model/code/rule versions, forecast window/unknown metadata,
   actual captured UTC, source excerpt SHA/URLs/publication/retrieval and previous-link
   chain are consistent. Run the read-only verifier above. It checks SQLite integrity,
   triggers, bounded whole ledger, request/payload metadata, chain and source content.
5. Make a scratch copy, reopen it, and use `prospective_capture.as_of` to prove the
   same frozen records survive restore. Compare the pre-pilot durable inventory: no
   deletions/rewrites of immutable historical rows, IDs, attachments, ACKs or receipts.
   Ordinary real source activity may add legitimate alerts: reconcile those changes
   to their primary-source IDs; prospective IDs must never enter any public payload,
   outbox, paper signal or delivery receipt. Verify normal Pages publish/ACK/lineage.
6. Disable capture immediately after the designated run completes, even if it failed:
   `MOZES_PROSPECTIVE_CAPTURE=0`/remove; clear the authorization variable afterward.
   Verify capture off. Never delete/update the permanent Git ref or its commit.
   An already queued successor cannot adopt the claim even if the global flag stayed
   enabled. Later denied runs may replace private health with SKIPPED; retain the
   designated run's artifact/health and verify its immutable observation directly.
   No automatic retries, second activation, revised forecast capture or sustained
   collection is authorized by this window. A fresh ID requires separate approval.

## One-shot mechanism and at-most-once argument

`prospective_claim` is the only new Git writer, inside the existing workflow lock.
It has `contents: write` and `actions: read`, no DB or notification credentials.
The monitor explicitly retains `contents: read` and its existing publication permission.
The grant step in the monitor performs only Git/Actions reads. No scheduler, worker,
DB writer, production migration or incident-recovery claim is added or reused.
The optional claim job and permit step are nonfatal, including setup/timeouts; ordinary
monitor success/checkpoint selection must not become a failed producer because an
optional claim failed. A capture SHA reaches the observer only when **both** claim
and permit steps succeeded. Checking a green workflow alone is insufficient.

1. The canonical ID binds a fixed issue time, approved code and unique nonce. The
   claim binds that ID, repository/workflow, scheduled run ID, attempt **1**, event,
   fixed single-attempt scope and claim-job ID. Main, run and job identities are
   verified against GitHub. A different event, code, dispatch or rerun is refused.
2. Following the incident's **pattern**, a new commit keeps the approved code tree
   and records only authorization metadata. A confirmed HTTP **201** create-ref at
   `refs/tags/prospective-pilot-<ID>` is the durable latch. There is no PATCH, DELETE,
   adoption or retry. Git's atomic create permits at most one winner. This namespace
   is independent of `monitor-recovery-*`. The Git metadata is repository-visible;
   it contains no forecast, source excerpt, model decision, database data or secret.
3. A complete, bounded inventory of scheduled runs for the ID's embedded code/time
   also rejects a prior started claim step. Thus a crash/timeout with **no ref** cannot
   cause a later run to retry an ambiguous request. Missing/incomplete history is a
   denial. The fixed one-hour expiry prevents reuse after normal Actions retention;
   modifying issue time or code changes the ID, requiring new approval.
4. Only a successful **new** claim step exposes its commit SHA as a job output. A
   read-only monitor step rechecks the permanent ref, exact commit/parent/tree and
   both job owners before creating a local permit. The observer atomically consumes
   it in `RUNNER_TEMP` before touching its DB. Files are not in checkpoints, restores
   or uploaded artifacts. Same-run process reentry, failure and permit-step restart
   are refused; a GitHub job rerun has attempt >1 and cannot use old claim outputs.
5. Exactly one allowlisted event can enter that attempt. Failure after consumption,
   partial archiving, failed checkpoint upload or an older restored DB cannot release
   the Git latch or authorize another run. There may be **zero successful records**;
   this guarantees at most one attempt, not successful delivery or automatic recovery.

Trust boundary: GitHub's atomic create-ref and run/attempt/job execution identities,
the reviewed main workflow and its isolated runner filesystem are trusted. Retain
claim-attempt Actions records throughout the one-hour window and permanent refs
indefinitely. An administrator deleting/reforging refs or claim-attempt history,
tampering with runner permits, or executing modified code is outside the guarantee.
Unavailable API evidence, permission restrictions or an expired/delayed schedule
fail closed without changing normal monitoring. No claim of successful collection
can be made from a green monitor alone.

The existing stale-recovery audit accepts the auxiliary job only when entirely
skipped with no steps. A started prospective claim still requires manual recovery
audit; the original incident claim and all its prior-attempt guards are unchanged.

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
PR35 stays HOLD; PR46 remains excluded. No claim of alpha,
validated strategy, complete historical knowledge or executable investment eligibility.
