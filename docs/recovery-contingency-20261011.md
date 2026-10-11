# October 11 review-only recovery contingency

Status: preparation only. No merge, activation, variable change or dispatch is
authorized by this PR. Abandon it if the original scheduled recovery starts.
Deadline remains October 12 00:00 UTC (03:00 Israel); no extension.

## Design and exact authorization

Reuse lightweight-monitor.yml, its workflow-level mozes-hot-monitor lock, existing
incident-eligibility job and permanent refs/tags/monitor-recovery-20261011-38052910145.
No new workflow, credentials, endpoints, permissions or dispatcher. The actual
event remains workflow_dispatch. Ordinary Edge dispatches have no new input and
retain ordinary restore behavior. An explicit emergency input enters eligibility;
if activation is off, the monitor job is also blocked rather than falling through.
Manual recovery stops if a newer checkpoint exists. Scheduled monitoring can then
use ordinary restore, preserving MAX_REWIND=45 minutes.

The separate human-set MOZES_INCIDENT_MANUAL_APPROVAL variable must equal:

    <EXACT_APPROVED_NEW_MAIN_SHA>:workflow_dispatch:38052910145:0ca9e7f01ccb639914820c96489192d8c5e7842d098e52de0c7fcbb6c19687fb

The deliberate dispatch input incident_recovery_authorization must equal
20261011:38052910145, priority_only must be true, and ALL source override and hot
event inputs must be empty. The approved-main pin, push CI, real main SHA, window,
run_attempt=1, provider/producer/receipt/ACK/pending checks apply to BOTH routes.
Eligibility repeats before atomic claim and before restore. There is one shared
claim for this incident, independent of route. A prior started/ambiguous claim
poisons retries even if its ref is absent; never delete or adopt it. A runner crash
after claiming consumes the authorization without permitting another attempt.

## Human approval and execution decision plan

1. Review the exact PR head, complete CI, diff and this runbook. Freshly inspect
   scheduled runs, claims, producer jobs and newest artifacts. If original recovery
   has started, abandon this contingency; do not merge or dispatch it.
2. Obtain explicit approval to merge this exact head AND deliberately authorize
   workflow_dispatch for ONE incident recovery. Existing schedule approval alone
   does not approve the alternative event or the new main SHA.
3. Under that separate approval, disable MOZES_INCIDENT_RECOVERY_ENABLED and leave
   manual approval unset before merging. Recheck queued/running producers and any
   started claim; disabling does not cancel an already-started workflow. Wait for
   any existing owner to finish and abandon contingency if it claimed recovery.
4. Merge only this PR. Record resulting main merge SHA; wait for successful push CI
   on exactly that SHA. No recovery can claim during the pin mismatch: context
   compares runtime SHA, actual main and the approved-main variable before writes.
5. Obtain/record exact resulting-main approval, then pin MOZES_INCIDENT_APPROVED_MAIN
   to that SHA. Freshly revalidate all original protected history, source artifact
   11669812295 from run 38052910145, checksum, complete producer evidence, no claim,
   current feed, immutable Edge/ACK fingerprints, pending identities, Worker
   11c60e3e-b948-43db-90af-35e1e86bb56e and provider write-capacity proof. Keep the
   strict ten-minute recency/current-hour positive analytics/headroom contracts.
6. With explicit approval of the two variable changes and ONE deliberate dispatch,
   set the SHA-bound manual approval above and activation to 20261011. Recheck claim
   and newest artifact immediately before dispatch; if a scheduled route acquired
   the claim first, do not dispatch. Both routes remain serialized; a race still
   cannot acquire two permanent claims. Do not cancel a recovery owner.
7. Dispatch lightweight-monitor.yml ON main once with the exact input above and
   priority_only=true. Do not use the old source_run_id/checksum inputs or supply a
   hot Edge event. If dispatch outcome is ambiguous, investigate the run/claim;
   do not repeat dispatch or retry a claim. Provider failure, stale evidence, audit
   contradiction or deadline expiry means STOP without weakening any gate.
8. Observe checkpoint provenance/integrity, preserved history/receipts/eight ACKs,
   no duplicate alerts, backlog, issuer sync, feed/Pages and next ordinary restore.
   Complete two healthy full UTC hours. Remove the manual approval and disable the
   incident activation after success under the approved cleanup procedure; retain
   the permanent claim forever. Ordinary scheduling returns through newer state.

## Rollback and security review

Before a claim: withdraw activation/manual approval. A source revert is optional,
requires its own reviewed main SHA and CI, and must not be mistaken for approval
to repin recovery. After a claim: never retry, delete the claim, rewind the database
or replay alerts. Preserve runner/artifact/claim evidence and request a separately
reviewed forward repair if processing failed. No Worker rollback is involved.

Untrusted dispatch inputs only select eligibility and are read as JSON, never
interpolated into shell commands. Approval binds event/source/checksum/main; branch,
rerun and window guards remain. Secrets remain scoped to existing provider steps;
no credential value is added to an input, output, approval variable or claim.
Repository write access is the trust boundary for setting approval variables and
dispatching; this is a deliberate human approval protocol, not cryptographic proof
of a particular human's consent. Permission to merge does not imply permission to
set variables or dispatch. CI runs offline and performs no provider/claim writes.

Residual risks: scheduler/manual races consume the same authorization, provider
freshness can expire while queued, and a crash after claim permanently blocks retry.
Those outcomes are safe stops, potentially requiring another separately reviewed
incident plan. This PR does not establish current production readiness by itself.
