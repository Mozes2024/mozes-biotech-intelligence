# Automatic Pages contention: bounded checkpoint reselection

Base: `2569ad87e18d66d162ccc9e22ef970800e63d65e`. This change is a
Draft implementation for review, not production activation or a publication ACK.

## Confirmed failure and reproduction

Automatic [39284648690](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/39284648690)
received the CI `workflow_run` event but exhausted three restore attempts at
16:22:35 UTC on October 19 with `producer_busy`. No export, deploy or ACK occurred.
Pre-merge [39278691187](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/39278691187)
failed with the same reason at 15:34:04 UTC, before GitHub's incident started.
Manual [39299938936](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/39299938936)
subsequently passed checkpoint, deployment, ledger and public-byte verification.
The known failure is ownership contention plus early retry exhaustion, not a
missing automatic trigger or evidence that staging/shadow changed the publisher.

The deterministic regression uses real `newest()` validation with simulated
GitHub inventories and a fake clock. A producer remains busy for 85 seconds:
the original policy fails at 0/20/40; automatic mode reselects at 0/20/50/90 and
then copies the verified newest checkpoint without modifying its source bytes.
Other tests cover a changing downloaded artifact and continuously busy queues.

Measured CI was 131 seconds, a later successful Pages run 146 seconds, and the
sample median monitor idle gap 125 seconds. Some idle gaps exceeded seven
minutes, but Edge requests and delayed cron delivery prevent a reliable forecast.
These are observations, not an SLA or proof of future quiet time.

## Alternatives and selected scope

**A: bounded reselection in the original automatic run — selected.** Use more of
the existing five-minute restore budget, rather than terminating after two short
waits. Keep the original event/SHA, lock, attempt status and publication pipeline.
The existing serialized monitor publication controller remains responsible for
later eligible requests after an exhausted run; this patch adds no dispatches.
Its two-minute request throttle and verified-success cadence are unchanged.
Those later workflow_dispatch publications are not reported as proof that the
original automatic workflow_run chain succeeded.

**B: new producer-completion scheduling — rejected for this narrow patch.** A
workflow_run event for every monitor completion would enter the existing Pages
concurrency group even when its gate later skips. Such a no-op can replace a
valid pending request, increase Actions consumption, or starve publication.
A durable scheduler would need a separately reviewed intent/attempt protocol and
coordination across automatic and monitor-requested publications. Simply adding
triggers or dispatching again is not a safe improvement. No such prototype or
new trigger is included in this PR.

Longer waits do **not** solve continuous contention. With no consistent quiet
checkpoint inside the budget, HOLD is the correct outcome. This implementation
improves transient contention recovery; it does not guarantee publication under
continuous Edge arrivals. If production evidence establishes persistent
starvation after this change, a broader scheduling protocol remains separate work.

## Exact policy and unchanged safety contracts

Only the existing automatic workflow_run path passes `--automatic` and the
current-head guard's exact `--expected-sha`. It has at most eight restore
attempts, with waits 20, 30, 40, then at most 45 seconds between conflicts.
All attempts, GETs, downloads, validation, local copying and waits share one
monotonic 300-second deadline. No wait starts if it would reach that deadline;
subprocess timeouts remain capped by the remaining budget. A local operation
finishing after the deadline cannot report successful restoration.

Each automatic attempt checks main before selection, and success checks it again
before returning to export. A superseded SHA, malformed metadata or failed API
call fails immediately. The final predeploy main guard is still independent.
Manual workflow_dispatch retains three attempts and 20-second waits.

Only `producer_busy`, `checkpoint_changed` and `inventory_changed` are transient.
Every retry calls the full existing `_restore_once()` path: complete inventories,
producer ownership and attempt validation, newest immutable artifact selection,
unique layout, expiry/provenance/chronology, SHA256, SQLite integrity and foreign
keys, then complete reselection before copying into a fresh consumer database.
There is no cached fallback or omission of old queued producers. All other errors
remain terminal. Neither `newest()` nor `validate()` is changed.

The CI-to-Pages trigger, whole-workflow `pages` lock, pending-request coalescing,
duplicate prevention, eight-minute build timeout, five-minute deploy timeout,
preflight, fingerprint/checkpoint ordering and ACK-after-deploy remain unchanged.
The monitor workflow, legacy writers, historical attachments, source artifacts,
Cloudflare, variables/secrets, notifications, pilot and investment gates are not
modified. META-M8 remains OFF/HOLD; PR57 stays independent and Draft.

## Cost, failure modes and rollout evidence

The heavy restore-attempt ceiling increases from three to eight, within the same
300-second wall budget. More complete inventory scans and current-SHA GETs can
consume additional API quota and Actions minutes. Adaptive waits reduce repeated
scans during a long-lived owner. This is a bounded increase, not measured savings.
Build setup/export/upload still need to fit the existing eight-minute job; a
large export or API outage can still cause the job to fail without deployment.
A new producer after restore can still block preflight, intentionally. A newer
main during waiting invalidates this attempt rather than publishing obsolete code.

The existing regression suites continue to cover duplicate dispatch suppression,
verified ACK after deployment, rejection of failed/skipped deployments and
preservation of immutable receipt/source history. New tests additionally prove
deadline exhaustion preserves an existing local publication ledger and never
creates a consumer database from a continuously busy inventory.

After a **separately approved merge**:

1. Record the merged SHA and successful push CI including test and worker-image.
2. Observe its automatic workflow_run Pages run; do not substitute a manual run.
3. Record each retry's reason, delay and code SHA. Verify restore reselected the
   latest completed checkpoint, then export and preflight succeeded.
4. Verify deployment and the subsequent unique ledger ACK artifact. Match the
   request, artifact, public receipt, public data bytes, source SHA, semantic
   fingerprint, checkpoint DB hash and source run/artifact identities.
5. Observe several ordinary monitor cycles and retain exhausted/failed runs as
   HOLD evidence. Never weaken the busy check to obtain a green result.

Synthetic tests and PR CI cannot establish production scheduling reliability.
Until that sequence is observed on merged code, automatic publication remains
unverified/HOLD. No main-CI rerun, Pages/monitor dispatch or production rollout is
performed to validate this Draft.

Rollback after a separately approved merge is a reviewed revert of these policy
and workflow changes, restoring the three-attempt automatic behavior. Preserve
all checkpoint files, immutable artifacts and deployment receipt history. Do not
delete data, replay notifications or change monitoring schedules as rollback.
