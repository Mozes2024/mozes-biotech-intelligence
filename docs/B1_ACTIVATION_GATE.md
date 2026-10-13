# B1 minimal production activation gate

Review-only proposal based on main `a50dfeadbbefd43af93c70abde03841e3521a335`.
No merge, deployment, production scan, recovery, replay, Worker or D1 change.

## Already active and the deployment gap

B1 classifier code and Pages publication are complete. Pages run `38425247266`
completed successfully at that main SHA on 2026-10-13 at 15:51:04 UTC.
Lightweight monitor run `38427029438` also completed successfully at that SHA.
Its checkpoint is `2026-10-13T16:03:07+00:00`, SHA256
`109a9179438954fa3f0cc60777ff501f1d492169ddd142a1d0e6efb32598178e`.
Hash binding and SQLite integrity pass, with zero foreign-key violations; the
checkpoint was inspected read-only. It contains 162 watch issuers (158 with CIK), 29 immutable
financing records, 101 legacy financing baseline keys, and zero periodic financing
coverage keys. Its latest financing operation started on 2026-10-06 at 06:33:25 UTC.
Deployment alone therefore did not start routine B1 financing classification.

The existing workflow supplies `SEC_USER_AGENT` from the repository variable.
Presence was verified through the authenticated variables API; its value must not
be included in evidence or logs. The new activation variable is currently absent.
No SEC endpoint was contacted to verify acceptance; this gate proves configuration,
not a successful live SEC response.

## Smallest activation mechanism

An opt-in call in `run_monitor` runs only on the existing priority path when
`MOZES_B1_FINANCING_OBSERVATION=1`. The lightweight workflow reads that value from
the repository variable. Missing/other values keep observation disabled.

The same `mozes-hot-monitor` lock, restored SQLite database, `mozes-live-monitor`
artifact, hash-bound checkpoint and `publication_control` handoff remain the owners.
No cron, job, writer, producer artifact or dependency is added. Fast source checks
precede the financing batch; existing reconciliation and outbox policy follow it.
Financing classification does not independently enable new push policies.

Each batch permits:

- One **attempt**, successful or partial/failed, per six hours in durable DB state.
- Up to five valid-CIK issuers from the existing priority list (capped at 40).
  Least recently attempted issuers go first; unattempted issuers keep their turn.
- Up to eight matching recent filings per issuer, the existing 45-day window,
  and up to four documents per filing; at most 40 filings and 160 documents.
- A 60-second work deadline checked between issuers, filings and documents and
  passed to existing SEC submissions/index/document transport. SEC retry waits
  and socket timeouts use the remaining deadline; existing response-size and
  rate limits remain. This is not a hard wall-clock cancellation of a slow body
  read or classifier already in progress; the workflow retains its 12-minute cap.

At most 205 source reads (five submissions + 40 indices + 160 documents), each with
the existing maximum three network attempts, can occur if the time budget permits.
Most normal batches stop well below that bound. Repeated hot ticks do no financing
I/O until due. This is bounded observation of priority issuers, not a claim of
whole-universe financing coverage. Forty issuers need eight fully processed
batches (48 hours between repeat visits); slow/failing sources can lengthen that.

## First refresh, partial failures and publication

The first enabled eligible batch immediately commits its UTC observation start
and attempt time **before** source I/O. The shared start applies even to issuers
visited in a later batch. Publication at/before that boundary is quiet coverage
for all forms; a genuinely later publication produces one change when new evidence
is inserted, including after an unsuccessful first scan or without a legacy baseline.
Existing per-issuer periodic coverage keys are retained. The legacy full-pipeline
classifier behavior is unchanged outside this opt-in observation path.

The interval from B1 Pages deployment to this first enabled observation is an
explicit unobserved gap. Publications in that interval may enrich initial context
quietly; no retrospective alerts, rewritten immutable rows or historical replay
are promised. No deployment timestamp is substituted for an observation that did
not happen. Initial empty/failed/partial scans cannot move the boundary on retry.

Index failures remain PARTIAL even when the fallback primary document succeeds.
Stored successful documents survive later failures. Attempt cadence is retained
on failure; no retry storm occurs. Document truncation/deadline exhaustion is
INCOMPLETE. The monitor operation preserves that status while exporting successful
evidence and saving the existing durable checkpoint.

The ordinary pipeline export follows the observation and reads the same DB.
Its financing fingerprint now includes facts and all provenance, so changes within
the same accession/kind also request publication through the current owner.
The existing publication/capture cutoff remains: newly captured financing appears
in card context only after the next UTC date boundary. Changes and operation health
are exported immediately; immutable evidence and cadence are checkpointed immediately.
Pages stays an offline checkpoint consumer. A failed publication remains eligible
for the existing future publication handoff; this task does not change that contract.

## Verification and separate GO/HOLD gates

Offline tests cover cadence boundaries/restart, failure rotation, missing identity,
old/new publications after failed scans, no duplicates, partial indices/documents,
issuer/filing/document/deadline bounds, opt-in ownership, payload and checkpoint
agreement, source-span facts/provenance fingerprinting and the retained PIT cutoff.
Existing B1 and financing-coverage contracts must remain green. Full exact-head
`ci.yml` includes Python, JavaScript, offline smoke and Docker; a branch dispatch
cannot publish Pages because that workflow accepts only a successful main push.

**Code GO** requires those checks at the final PR head. **Production HOLD** remains
until separate merge and activation authorization. No configuration is changed here.
After authorized merge, the smallest operational step is setting the repository
variable `MOZES_B1_FINANCING_OBSERVATION` to `1`, then allowing the next ordinary
serialized monitor run. No manual scan dispatch is needed. Review that run's
financing operation, durable artifact and later Pages export before claiming live
observation. Source acceptance and actual coverage remain unverified until then.

Rollback is clearing/disabling only that variable. Preserve the checkpoint,
observation start, issuer attempts, immutable evidence and all change identities.
Never reset state or start recovery/replay as rollback.

After this bounded gate, the Five-LLM product roadmap resumes at **B2 Regulatory
Intelligence**: source-backed adversarial contracts must distinguish intent,
designations, submissions, acceptance and approvals. A5 activation and the held
infrastructure/recovery items remain outside B2's product scope.
