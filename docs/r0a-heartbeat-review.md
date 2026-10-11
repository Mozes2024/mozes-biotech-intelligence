# R0a review and rollout boundary

Baseline: `527133b98b68cc64a4f72afeabfed0916eff81ae`. This PR does not deploy.

The existing global HotEdge DO owns the watchdog inside its serialized alarm. It
checks GitHub at most once per five minutes, or ten minutes following failure.
There is no D1 query/write in the watchdog. Source polling retains the existing
120-second alarm interval. A due watchdog owns one dedicated alarm cycle: at most
23 external GitHub calls, with no concurrent source/analysis/backfill/delivery
fetches. Sources resume on the next alarm with their deadlines/cursors unchanged.
This adds one alarm (120 seconds) plus bounded heartbeat I/O to due source work;
23 sequential requests each have an eight-second timeout (184 seconds total).
Normally one cycle per six minutes is reserved, or ten minutes after a blocked
check, plus I/O duration. Primary-source cycles
keep their original budgets and policies; they cannot indefinitely defer liveness.
Health exposes `monitor_heartbeat_reserved_cycle`. Missing-token checks perform
no external calls and therefore do not reserve a source cycle.

Health requires an unexpired nonempty `mozes-live-monitor` artifact, GitHub's
SHA256 artifact digest, first-party main lightweight workflow provenance, a
completed first attempt, and successful database-binding and artifact-upload
steps. This is GitHub durability metadata, **not a Worker-side SQLite inspection**.
The existing producer independently checks the downloaded database and lineage.
Missing/ambiguous metadata, reruns, newer deep artifacts, inventory bounds or API
failures block watchdog dispatch. No older artifact is substituted.
The artifact inventory bound is 1,500, allowing the three-day retention window
at the observed event-retry plus nominal schedule cadence; beyond it, fail closed.

A checkpoint aged 20–45 minutes is eligible when all five active Actions states
are empty. Immediately before an ordinary POST, DO storage records the attempt.
The same timestamp is advanced before existing event/gap POSTs; those routes keep
their existing inputs/retries. Successful POST means accepted, not checkpointed
or acknowledged. An ambiguous POST reserves the ten-minute throttle. A future or
invalid stored timestamp blocks. No incident input, source override or event ID
is present in the watchdog POST. GitHub's existing concurrency group remains the
final processing serializer; a separately arriving GitHub schedule can still
race the read/POST boundary. The watchdog cannot make that external race atomic.

Active owners suppress dispatch. An owner aged at least 20 minutes is reported as
OVERDUE_OWNER with run ID, state, age and an explicit operator action. This is an
escalation diagnostic, not cancellation, permission to duplicate a run or a
checkpoint. OVERDUE_OWNER/BLOCKED also make public health PARTIAL even when source
polls are OK. Missing/future owner timestamps fail closed. The 45-minute checkpoint
boundary still blocks; an overdue GitHub runner cannot be repaired by this code.

After 45 minutes this path stops. It cannot repair a prolonged GitHub outage or
relax MAX_REWIND; R0c requires separate review.

## Existing credential evidence and remaining rollout gate

Protected settings evidence confirms a `GITHUB_TOKEN` secret_text binding; the
reviewed configuration fixes this repository, main and lightweight-monitor.yml.
Real ordinary event runs prove the deployed integration can dispatch. The token
value has not been retrieved. The configuration recommends repository-scoped
Actions Read/write, and GitHub documents Actions Read for workflow/artifact/job
GETs and Actions Write for dispatch. Actual Actions Read authorization of the
deployed secret is **not independently proven by dispatch success**.

Before a separately approved deployment, verify those GETs securely with the
existing secret in its runtime, and obtain sanitized PASS evidence. Stop on
401/403/429 or incomplete evidence. Do not provision/broaden credentials as part
of this PR. No new permissions, dependencies, secrets, migrations or infrastructure.

## Proposed approved rollout

1. Review exact PR head and green full CI; merge only with separate approval.
2. Establish existing token read contracts, compare production bindings/settings
   and retain the singleton namespace and 120-second interval.
3. Separately approve Worker deployment; preserve event identities and all ACKs.
4. During a genuinely quiet interval observe FRESH → one ordinary accepted run →
   new bound durable artifact. Verify the next producer restores that artifact,
   publisher cooldowns, receipts and no duplicate alert/outbox entries.
5. Stop on BLOCKED/OVERDUE_OWNER or GitHub queue delay approaching 45 minutes.

Rollback is a separately authorized redeployment of the previous Worker bundle.
Retain DO dispatch timestamps; never delete a recovery claim or restore historical
data. No production action has been taken during preparation.
