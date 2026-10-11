# R0b consumer boundary

Baseline: `527133b98b68cc64a4f72afeabfed0916eff81ae`. Independent of R0a.

Pages consumes the newest durable artifact by artifact creation time, including
expired artifacts in selection. It inventories main lightweight producers,
including older dispatched waiters updated after that artifact. Successful binding
without an available uploaded artifact, incomplete/active processing, reruns,
unknown provenance or bounded/inconsistent inventories block publication. No
fallback to an older artifact. A newer deep-owned artifact blocks; deep ownership
is deliberately not redesigned here.

The artifact must have a unique DB, checkpoint and manifest; exact repository,
branch, workflow path, run/attempt and source SHA must agree. Manifest → checkpoint
→ artifact chronology, internal database SHA256, full SQLite integrity and foreign
keys are checked. Discovery repeats after download; an identity change aborts.
SOURCE_RUN_ID is advisory and cannot select an older snapshot.

Only an empty ephemeral consumer destination is populated. No authoritative
manifest/checkpoint is generated or copied into the producer namespace. Export
opens SQLite mode=ro, skips schema/bootstrap/catalog/import writes, and writes only
public export files and the ordinary publication request. Database bytes remain
identical; no source scan, alert dispatch, delivery receipt or ACK is executed.

The exported checkpoint time and age refer to the source checkpoint, not the
export time. All UI pages display age and an explicit stale warning after 45
minutes; the browser recomputes age on render. Missing/invalid checkpoint time is
also stale. A three-hour snapshot can be published truthfully. The producer's
MAX_REWIND and existing current-main Pages guard remain unchanged.

Inventory bounds are 1,500 named artifacts and 40 relevant producers. Workflow
history is scanned completely, including every old queued run and recently updated
rerun, rather than failing permanently after 3,000 lifetime runs. The initial
advertised count bounds pagination; a changing count, duplicates, missing rows,
unknown status or a 90-second inventory budget exhaustion fails closed. Each API
request has a 30-second timeout. This is still a full-history scan, not a constant-
cost time-window query: sufficiently large/slow inventories can exceed the time
budget and must be reported, never truncated to make publication pass.

Natural active-producer conflicts, changing inventory counts and checkpoint
changes permit at most three complete attempts, with 20 seconds between attempts.
Every attempt reselects, downloads, checks provenance/SHA256/SQLite and rechecks
the newest checkpoint before copying. Completed but incomplete job evidence,
reruns requiring review, missing artifacts and failed integrity checks are not
retryable. Continuous activity can still exhaust these attempts; no monitor is
dispatched or suppressed and no older candidate is published as a fallback.
This is a consumer, not proof that monitoring is healthy or safe to resume.

## Proposed rollout, after separate approval

Review exact-head CI and approve R0b merge first (no Worker deployment required).
Observe the normal Pages path using a fresh and then a quiet/stale checkpoint.
Compare selected artifact/run/internal checksum and historical record identities;
confirm the public stale banner agrees with checkpoint time, while browser alert
delivery and ACKs remain unchanged. Preserve the current-main publication guard.

If consumer validation fails, Pages stays on the last deployment and reports a
failed build. A separately reviewed code revert returns the original Pages route
(including its existing stale-data failure); it does not replace monitor data.
An API race after final verification can still advance the producer while the
consumer publishes its verified snapshot. The visible checkpoint time makes this
truthful; no consumer action can rewind authoritative state.
