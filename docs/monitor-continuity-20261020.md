# October 20 producer continuity incident — recovery and activation NOT executed

Read-only evidence, 2026-10-20 UTC. This is a new incident, separate from #74–#76.

## Cause and production boundary

Main is `106fa0532ac52256030c975cfa965365d7eff203`. The last producer,
[39474053120](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/39474053120),
uploaded artifact **12289430486** at 18:24:16. Its internal checkpoint and manifest
are 18:24:11 UTC. The next monitor run,
[39483161189](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/39483161189),
started at 19:24:17 and failed restore with:

```
stale checkpoint requires reviewed forward recovery and explicit SHA256
monitor state restore failed; refusing empty/old bootstrap
```

Every source-processing/checkpoint/upload/ACK/publishing step was skipped.
There was no intervening producer attempt, dispatch failure or competing owner in
Actions. Scheduled runs occurred at 17:19 and 19:24 despite the configured
15-minute fallback. GitHub documents schedule delays/dropped jobs; its internal
reason for this particular gap is unknown:
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

Active Worker **4d60c332-f2fb-40fe-8994-c0d5b0ac7cf6**, deployed at 18:26:14,
is Stage A. The downloaded production script contains neither idle-watchdog,
heartbeat nor source-gap reconciliation. Neither watchdog flag is configured.
Main's gap-only/opt-in feed-hint code is therefore not evidence of deployment.
PR #25 remains open/HOLD, with no production activation. The actual script has
the forward fence, no Stage B `source_item_id`, and production D1 has only
migrations 0001–0004. `EDGE_FORWARD_ONLY_FROM=2026-10-20T18:24:29.424Z` and the
120-second interval remain unchanged; cron is `*/2 * * * *`.

All four Edge source polls continued successfully through the diagnosis, with
332 active verified issuers. The retained backlog had 33 historical/manual-review
events, no eligible/awaiting enrichment and one blocked analysis. With no new
eligible event, the Stage A event dispatcher supplies no producer liveness.
An Edge poll or alarm is not a GitHub checkpoint. No provider authorization error
was observed in the production gap; future heartbeat GET permission is unverified.
The old disabled credential probe was not repeated.

## Retained lineage and authentic publication

Database SHA256:

```
94bda318e9f8e553709b295d5249cebf269002cefc4d718c0cd51f1dc21192c1
```

The full downloaded database matches the hash-bound checkpoint. SQLite integrity
and foreign keys pass. It retains 1,995 changes, 50 Edge links, 3,632 observations
and 100 outbox rows: 98 sent and two dead, all on the log channel, with no
pending/sending/failed rows. All 50 D1 completion ACKs and 50 public-feed changes
are represented; per-channel sent timestamps agree. RAPP's original completed
identity/ACK is retained. The complete bounded producer-history gate found only
39483161189 after the checkpoint, proven to have skipped all processing.
Private raw artifacts/D1 evidence stay outside Git. Recheck these point-in-time
facts under the existing workflow lock immediately before approved recovery.

[Pages 39480745985](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/39480745985)
has an independently verified successful deploy and post-deploy schema-2 ledger
ACK for this exact producer/artifact/hash. Its 19:09 publication did not renew
the 18:24 producer checkpoint. Existing source coverage also remains partial:
the last producer reported genuine publisher/IR failures, which recovery cannot
turn into successes.

## Targeted prevention candidate

`MonitorHeartbeat` reuses #25's GET-based artifact/owner proof, with no relay or
POST-only fallback. On the existing singleton DO alarm, an explicit
`EDGE_MONITOR_HEARTBEAT_ENABLED=1` enables a bounded check every ten minutes.
It validates the newest artifact inventory, digest, main/workflow/attempt, and
successful bind/upload steps. It distinguishes a successful producer from a
durable checkpoint left by a failed producer. It queries every active owner
status and detects queued/overdue owners. Only a 15–30-minute checkpoint with no
active owner can request the existing `priority_only=true` workflow. The normal
45-minute restore guard and monitor/Pages shared queue are unchanged.

A durable reservation permits at most **one generic automatic dispatch per
authoritative checkpoint**, including across restart, remote timeout/500 and
eventually consistent owner lists. A new validated artifact allows the next
cycle. A missing/failed dispatch without a new checkpoint is explicitly
`DISPATCH_UNCONFIRMED`; it requires inspection, not a blind repeat scan. 204 is
`DISPATCH_ACCEPTED_UNVERIFIED`, never producer success. Missing/expired/ambiguous
evidence, rate limits, inaccessible GETs and stale checkpoints fail closed.

Checks have at most 15 artifact pages (1,500 records), two producer proof reads,
five owner reads and one dispatch: at most 23 GitHub requests/check, 3,312/day
at the ten-minute cadence, zero new D1 queries/writes. An exhausted inventory
bound reports `BLOCKED/inventory_bound`; it must not silently fall back to an old
artifact. This conservative inventory cap and GET-capable existing credential are
rollout limitations, not verified availability guarantees. No credential or
permission change is included. A failed GET stops dispatch and reports PARTIAL.

Each due check has its own request budget. It rearms the **same** DO alarm for
one second later so normal source polling resumes promptly, instead of skipping
an entire 120-second source cycle. There is no second scheduler. Disabled mode
does no GitHub work. There are at most 144 extra control alarm invocations/day.
The public health response reports producer state separately from source health.

The dashboard summary explicitly says recent source status is unknown when any
configured source observation is stale or the inventory is absent, and includes
the last successful source observation. Current zeros no longer imply health.
Expanded details retain failed/partial and recovered-error history.

`scripts/build_stage_a_continuity.py NEW_DIRECTORY` first reproduces the existing
immutable stabilized Stage A build, then changes exactly three source files:
`hot-edge.js`, `index.js`, and the new `monitor-heartbeat.js`. Tests compare all
files to that baseline and run the original Stage A history/fairness/fence/MESO
suite using only migrations 0001–0004. Main's Stage B entrypoint and migration
0005 must never be deployed to this database. No production deployment,
configuration activation, schema change, reset, replay, ACK or channel change
has been executed by this incident response.

## Exact production boundary — requires new human approval

No old authorization applies. The reviewed forward-only action is **one new**
main-branch run, never an old-run rerun or an historical Edge event dispatch:

```sh
gh workflow run lightweight-monitor.yml --repo Mozes2024/mozes-biotech-intelligence \
  --ref main -f priority_only=true -f source_run_id=39474053120 \
  -f recovery_checkpoint_sha256=94bda318e9f8e553709b295d5249cebf269002cefc4d718c0cd51f1dc21192c1
```

Its existing restore code repeats producer/Edge/feed/history/hash gates under
the shared lock. A changed newest artifact, hash, ambiguous owner/receipt or
processing without a checkpoint invalidates this action. No broad incident
activation flag, historical event ID or old permit is supplied.

Prevention deployment/flag activation is a separately named consequential action
within any requested approval: review/merge this PR, build the exact Stage A
candidate, verify its unchanged bindings/configuration, and deploy that bundle
once using pinned Wrangler 4.149.0 with `--keep-vars`,
`--var EDGE_INTERVAL_SECONDS:120 --var EDGE_MONITOR_HEARTBEAT_ENABLED:1`.
Preserve the existing forward timestamp; do not move or recreate it. Existing
credential GET availability is not proven by offline tests. If the first real
check reports 401/403, stop at that exact blocker without token probes, permission
changes, blind dispatch or automatic deployment retry. No claim of unattended
reliability is justified until real producer/checkpoint/publication cycles pass.

After authorized resumption and activation, require at least three new genuine
successful producers, each with a new validated artifact/database checkpoint and
authentic Pages ledger ACK. Check cadence, continuing Edge polls/issuer coverage,
unchanged D1 schema/settings and historical receipts including RAPP. Ordinary
runs use the new latest lineage without the recovery override. CI, Pages alone,
health heartbeats and offline tests do not resolve this production incident.
