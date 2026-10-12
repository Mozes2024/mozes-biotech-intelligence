# Pages success acknowledgement reliability — 2026-10-12

## Confirmed incident

Base main: `51a5535f66a5b016d487c69186bd36ab07acb7a3`.

- Pages run 38206811086 deployed successfully at 05:12:42 UTC and uploaded immutable ledger artifact 11727412912 at 05:12:44. Its legacy receipt contains only ledger `1321db0146ae8bd38cdc352b163b9d3e0a2416c168104cdee082f83002087882`.
- Run 38207651227 logged `producer_busy` on attempts 1 and 2, then failed with **inventory_changed** at 05:25:14. No export, deployment or acknowledgement occurred.
- Monitor 38211466270 committed checkpoint 06:15:42 UTC and uploaded artifact 11729521306 at 06:15:46. Database SHA256 `96e3a4bfcafe116288f84cbaf612b307d1527603acbbce808f48c9fd58b4c213` matches the checkpoint; SQLite integrity returned `ok`.
- That artifact's manifest says `should_publish=false`. The database's `publish_request.at` is 05:22:19.648115 UTC, with the same semantic fingerprint as the newer manifest. The failed request therefore suppresses unchanged-data freshness for an hour, despite no deployment acknowledgement. The browser truthfully warns after 45 minutes.

Evidence: https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38207651227 and https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38206811086.

## Correction and invariants

- Producer eligibility compares semantic content and age against the **verified published source checkpoint**, targeting 1,500 seconds. A request does not move that clock. Legacy receipts acknowledge ledger content only and trigger the first upgraded freshness publication.
- `publish_request` remains the existing authoritative database state, now explicitly a two-minute dispatch reservation. The serialized monitor commits it into its immutable checkpoint before dispatch. Failure/cancellation expires the reservation after two minutes; it cannot silence the publisher for an hour. An explanation-only change is picked up by the next monitor's semantic comparison rather than bypassing the reservation.
- The dispatcher inventories all Pages requests, blocks duplicates while any request is queued/running, and retains a two-minute terminal/request throttle. Diagnostics distinguish requested/running/failed and independently verified success. A blocked publisher never delays/cancels the producer or changes alert-feed timing.
- Schema-2 receipts bind Pages run/code SHA, semantic fingerprint, ledger, source producer/artifact/database hash and source checkpoint time. Restore verifies completed first-party first-attempt workflow ownership, successful deployment and acknowledgement steps, unique nonexpired ledger artifact, and chronology. The receipt bytes remain immutable; no previous receipt is rewritten.
- The entire Pages workflow is serialized with `cancel-in-progress: false`. This prevents a later request cancelling a valid running build; the pending slot coalesces requests. Every build selects the newest authoritative checkpoint, and preflight immediately before deployment rejects superseded code, moved artifacts, unknown success evidence and checkpoint downgrade. One concurrency group covers deployment and acknowledgement, so an older pending request cannot deploy concurrently over a newer published receipt.
- Checkpoint restore retains exactly three attempts and 20-second waits, with a five-minute overall subprocess deadline and 60-second download limit. Complete fixed-count inventories run with at most four concurrent readers to shorten the mutation window. Missing, duplicate, changing, expired, untrusted, corrupt or incomplete evidence still blocks publication. Exhaustion reports `publication_blocked`; it does not mark the page fresh.
- Deployment preflight has a three-minute read budget inside the existing five-minute deploy job. Build timeout remains eight minutes. SHA256, SQLite integrity/foreign keys, chronological provenance, old queued producers, rerun rejection, alert/event identities and the 45-minute warning remain intact.

## Validation

The failure regression executes the exact original `main` function from base main: it fails because unchanged data plus an aging successful checkpoint yields `should_publish=false` after a recent failed request. The same test passes with this patch.

Focused suites cover success-only cadence, missing/legacy/invalid acknowledgement, failed/cancelled/queued requests, receipt ownership and chronology, source movement, downgrade, complete inventory, producer busy/reselection, corrupted/expired/missing artifacts and read-only consumer preservation. JavaScript watchdog, feed and hot-edge suites passed; all web JavaScript syntax and Python compile checks passed.

Initial full local Python run: 864 passed, 2 failed in 278.69 seconds. Both timing-sensitive failures (`test_promotion_v2` timestamp comparison and `test_v2d_foundation` one-second thread wait) passed on focused rerun and on unchanged main. The promotion timestamp behavior is an existing A3 concern, not changed here. Final full rerun and exact-head Linux CI results are recorded in the PR. Docker is unavailable locally; the existing `worker-image` CI job owns build/package verification. No production monitor, recovery, replay, alerts, migrations, Worker deployment or secret operation was invoked.

## Resource and latency boundary

- Measured current workflow inventory: 1,950 producer runs, requiring 20 API pages for a complete scan; artifact pages and relevant jobs are additional. No lifetime-history guard is removed. A live selection probe made 10 GET requests in 9.52 seconds before rejecting a changing inventory; it performed no download or write.
- Fixed inventories make the same number of API requests as before, with at most four in flight. A restore success performs two full selections plus one artifact download; preflight adds one full selection and verified receipt retrieval. Receipt validation adds two metadata GETs to an existing receipt download. Pages dispatch adds a complete Pages-run inventory when a publication reservation is eligible.
- Existing successful build/deploy jobs took about 106/14 seconds (about three rounded billable runner minutes). No production Actions-minute measurement exists for this unmerged patch. Targeting 25 minutes can produce approximately 48–58 freshness builds/day on frequent healthy monitoring, rather than 1,440 minute builds; content changes may add builds. Existing request and ledger artifacts are reused, with a small metadata increase and unchanged retention. The observed monitor artifact is 36,508,026 bytes; preflight does not download it a second time.
- Freshness eligibility begins at 25 minutes of **source checkpoint age**. Healthy frequent monitoring plus the observed two-minute publication path targets roughly 25–30 minutes. A 15-minute fallback schedule can take longer; a conservative bound after a successful eligible monitor is eight build plus five deploy minutes, plus Actions queue delay. Three producer conflicts terminate within the five-minute restore budget and retry on later eligible monitors. Continuously busy producers, changing inventories, outages and queue delays have no guaranteed publication latency and remain visibly blocked/stale. These are bounds/estimates, not a production SLA.

## Rollback

Before approval, close the PR; no production behavior has changed. After a separately approved merge, revert this PR's commit through a reviewed revert PR and its normal CI. Preserve all monitor databases, artifacts, legacy/schema-2 receipt history and event identities. The older consumer safely ignores schema-2 checkpoint fields and reads the original ledger key. Rollback reintroduces the old cadence limitation; do not raise the stale warning threshold or activate recovery.
