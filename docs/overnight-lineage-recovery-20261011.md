# October 11 lock-owning recovery — preparation only

This change is disabled until separately reviewed activation. It makes no Worker,
D1, scheduler, source-policy or notification configuration changes. The existing
conditional approval covers one recovery, not approval of these new privileges.

## Design and permissions

Reuse the existing `lightweight-monitor.yml` schedule and workflow-wide
`mozes-hot-monitor` concurrency lock. The incident job performs read-only gates,
then creates a permanent Git tag claim. The same workflow's monitor job repeats
the gates and restores the pinned database before processing. It does not dispatch
or wait for another workflow. No additional cron or recovery workflow is created.

Existing `actions: write` cannot create an atomic repository-wide claim. Only the
incident job adds `contents: write`; it uses `actions: read`. The monitor retains
its existing `contents: read` and `actions: write`. Checkout in the incident job
does not persist credentials. The claim is `refs/tags/monitor-recovery-20261011-38052910145`.
Its commit has the unchanged main tree and records source/checksum/run/head in its
message. Only a confirmed atomic **create**, never update or adoption, enables
processing. Do not delete or move this tag, rerun the claimed workflow, or reuse
its checkpoint override.
Manual source overrides for this incident also enter the claim-owner guard and
cannot bypass it. Ordinary monitoring does not receive the new read credential.

The repository currently has no Cloudflare read credential. Fresh deployed code,
settings and quota verification cannot be obtained through `EDGE_SYNC_TOKEN`.
Activation needs a new `CLOUDFLARE_RECOVERY_READ_TOKEN` Actions secret, scoped to
account `75b91ca20508a72979cb5337ce1cc7e2`: **Workers Scripts Read**, **D1 Read**,
and **Account Analytics Read**, with no Edit/Write permissions. Give it a short
expiry after the incident window. No secret is created or requested in chat by
this PR. Verify these read endpoints with the scoped token before activation;
permission failure stops the incident. Official references:
[permission groups](https://developers.cloudflare.com/fundamentals/api/reference/permissions/),
[analytics authentication](https://developers.cloudflare.com/analytics/graphql-api/getting-started/authentication/api-token-auth/),
[Worker content read](https://developers.cloudflare.com/api/resources/workers/subresources/scripts/subresources/content/methods/get/).

## Gates

- Only a first-attempt `schedule` run on the exact separately approved main SHA,
  with successful main push CI. Manual events/reruns cannot enter this path.
- Earliest execution October 11 00:00 UTC / 03:00 Israel; eligibility closes at
  October 12 00:00 UTC. Delayed runs do not shift that window.
- Source `38052910145`, artifact `11669812295`, ZIP
  `8157f00682d5c415f6fca51e7d6341b714cd9d52fdf5da80b51be6e480f6c447`, internal DB
  `0ca9e7f01ccb639914820c96489192d8c5e7842d098e52de0c7fcbb6c19687fb`.
  Require original successful producer attempt, unexpired artifact and unchanged
  provider digest; existing restore verifies database hash, manifest and SQLite.
- Worker `11c60e3e-b948-43db-90af-35e1e86bb56e` at 100%, approved bundle hash,
  exact effective settings fingerprint, 120 seconds, cron `*/2`, existing D1/DO
  bindings and secret names; only migrations 0001–0004.
- Provider analytics must show positive rows-written for the target database in
  the **current UTC hour after reset**, with aggregate account writes below 90,000.
  Successful reads/reset clock/previous-hour writes do not qualify. Queries are
  read-only SELECTs with `rows_written=0`. No synthetic capacity test is performed.
  Analytics lag can conservatively postpone or prevent recovery; the evidence
  demonstrates legitimate automatic writes, not per-request zero-write sync.
- Preserve 27 Edge records/eight ACKs, original public-feed revision and 40 alerts.
  Existing restore independently verifies all linked ACK/change IDs, published
  receipts and absence of nonterminal outbox state. The pinned full DB preserves
  1,099 changes, 106 archives, 40 outbox records and 38 delivery rows byte-for-byte.
- All five ALMS/VIR/GKOS×2/SGMT events must retain source/ticker/material identity,
  complete analysis, no completion ACK or ambiguous delivery. Validate primary
  source URL without fetching blocked publishers. Do not alter retries/cooldowns.
- Complete existing producer audit, including 38061061883/38071513265 and all
  subsequent attempts. Ambiguous writes, hidden reruns, incomplete evidence,
  active competing producers or a started previous incident claim stop recovery.
  A newer artifact disables the incident override: ordinary restore independently
  verifies its provenance and the unchanged 45-minute MAX_REWIND behavior.
- Before processing, verify the claim belongs to the current run/main/checksum,
  repeat provider/history/receipt/pending gates, then repeat original restore gates.
  External notification configuration must remain disabled.

## At-most-once and crash handling

Before capacity proof, cron runs may wait without claiming or processing.
The immutable ref survives runner loss. Additionally the full producer/job audit
rejects any previous **started** claim step, including failed/ambiguous creation
where a reference is absent. Therefore no subsequent cron can retry a possibly
consumed claim. Ordinary disabled incident jobs and unstarted/skipped claim steps
are explicitly distinguished from attempted claims. Missing job evidence fails
closed. GitHub's serialization plus atomic ref creation excludes simultaneous
claim winners. Do not configure automatic retries for the claim/API operations.

If the runner crashes after claiming, processing is NO-GO pending human review.
If processing/delivery fails, preserve its logs, tag, newest artifact and receipts;
do not delete the claim, replay notifications or restore an older checkpoint.
The existing always-checkpoint/upload and ACK-after-upload ordering is preserved.

## Exact activation requirements — separate approval required

1. Review this PR and full CI; explicitly approve merging its exact head AND the
   incident-only `contents: write` privilege and read-only Cloudflare secret.
2. After approved merge, wait for green CI on main. Confirm no active/conflicting
   monitor, newer checkpoint, ambiguous receipts or existing incident tag.
3. Provision/test the scoped read credential securely. Set repository variable
   `MOZES_INCIDENT_APPROVED_MAIN` to that exact merged main SHA.
4. Separately approve activation, then set `MOZES_INCIDENT_RECOVERY_ENABLED` to
   `20261011`. This is an authorization switch; merging alone cannot activate it.
   No activation/secret/variable/production mutation is done by this PR.
5. At the next genuine scheduled execution after 03:00 Israel, eligibility runs
   under the lock. Existing minute-7/22/37/52 schedule is unchanged. GitHub may
   delay cron; **03:07 is a nominal opportunity, not a guaranteed start**.
6. With capacity missing, wait read-only for later automatic cycles. With every
   gate passing, acquire exactly one claim and process in the same workflow.
7. Observe checkpoint upload, preserved history/receipts/eight prior ACKs, actual
   new deliveries, browser feed and Pages. Observe subsequent automatic runs
   restoring the newest checkpoint without recovery inputs. Record differential
   sync and two complete healthy usage hours separately. Never manually replay
   the five pending events or ingest SEC gaps.
8. A newer durable artifact logically closes the incident. Remove the activation
   variable/incident code through separately reviewed cleanup later; never remove
   the claim. If no new artifact follows a claim, stop for reconciliation.

Technical GO is conditional on reviewed code/CI. Production activation is NO-GO
until the new privileges/configuration are explicitly approved and provisioned.
No Stage B, migration 0005, billing, Worker deployment/rollback, interval change,
ATOS correction, SEC ingestion or external delivery is authorized here.

Local workflow lint: actionlint 1.7.12 does not recognize the already-existing
`concurrency.queue: max` key. The unchanged key is preserved; a disposable lint
copy omitting only that key passes actionlint. This is not a claim of complete
runtime validation of the original queue setting.
