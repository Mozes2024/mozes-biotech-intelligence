# BIO implementation tracker

Baseline: `51a5535f66a5b016d487c69186bd36ab07acb7a3`, reconciled 2026-10-12.
This updates the same tracker path prepared in #25; it does not merge its held code.
Pages reliability #29 has green exact-head CI at `5da8cd478ad1aeae722de36f12458e4bc9eb590c`
(run 38215330661: 868 Python tests, JavaScript, D1, smoke and Docker). Neither PR is deployed.
This is the single implementation tracker, not another audit. The four requested
roadmap/audit documents were not found in the searched Desktop, repository,
attachments and protected evidence locations. Rows below use the supplied master
mission and inspected code/PRs; they do not reconstruct the missing 91-finding
register or invent reviewer findings. Import its identifiers here when available.
Labels A1/A2 differ between the earlier audit and the master mission: META-M2 is
PR21's conservative registry identity work, called A2 in the master mission.

| Item / severity | Evidence state | Implementation | PR | Dependency / risk | Next action |
|---|---|---|---|---|---|
| R0b safe Pages consumer / P0 | Verified | Merged, published | #26 | Producer provenance, no older fallback | Retain checks |
| Conflicting deep schedule / P0 | Verified | Merged, cron suspended | #27 | Temporarily loses deep research; manual deep runs prohibited | R2 ownership before resumption |
| Pages producer races / P0 | Verified | #28 merged; later failure is inventory_changed after two producer_busy conflicts | #28 | Complete inventories remain mandatory | Retain fail-closed checks |
| Pages success cadence / P0 | Artifact + logs verified | Review-ready, exact-head CI green | #29 | Failed requests cannot acknowledge freshness; source ages remain truthful | Approve #29 first; no deployment proof claimed |
| R0a quiet heartbeat / P0 | Partially verified | HOLD, not deployed | #25 | GET probe failed; 204 is not completion | Complete correlated start/owner/failure reconciliation before POST-only dispatch |
| R0a checkpoint feedback / P0 | Offline implementation | Disabled-by-default trusted relay + monotonic DO receipt | #25 | Existing shared control secret trusts relay; receipt is not ACK or restore authority | Review/CI; no activation yet |
| R0c safe producer resumption / P0 | Pending design | Blocked high-risk task | — | Completed incident claim must never be reused; MAX_REWIND unchanged | Separately reviewed side-effect audit |
| R2 deep research / P1 | Mission priority; conflicting schedule remains suspended | Pending authoritative-writer design | — | No second writer, ad hoc deep runs or recovery | Prove ownership before resumption |
| Queue fairness / P1 | Merged code; deployment coverage unverified here | Merged | #16 | Merge does not prove deployed Worker coverage | Separate read-only deployment comparison |
| R1 ALMS 403 + SGMT/VIR/GKOS backlog / P1 | Mission + prior source failures; current state not reaudited here | Pending | — | Independent liveness first; no synthetic ACK/proxy bypass | Read-only reconciliation after R0a |
| META-M2 / A2 clinical identity / P1 | Verified | Merged in current main | #21 | Preserve exact NCT, registered aliases and canonical IDs | Completed; do not repeat |
| A1/A4 missing evidence / P1 | Baseline regressions reproduced: prior-only negative inference + score=None gate crash | Independent implementation, offline fixtures and read-only/PIT checks | #30 | Missing clinical/financial inputs suppress positive candidates; observed negatives retained | Complete exact-head CI and review after #29 |
| A5 negation/conditional FDA/excerpts / P1 | Existing work, review still needed | Open | #17 | Shared Python/Worker adversarial fixtures | Adapt valid work after identity PR |
| A3 atomic promotion / P1 | Helper commits cross boundaries; repeated promotion timestamp changed across a clock second in local suite | Pending | — | Interrupted/repeated writes must remain consistent | Frozen-clock replay + rollback/interruption regression before patch |
| B1 financing / P1 | Mission priority, not independently reverified here | Pending | — | Shelf/ATM capacity cannot be cash received | Verify fixtures and focused correction |
| B2 regulatory / P1 | Mission priority, not independently reverified here | Pending | — | Intent/designation cannot become approval | Source-backed adversarial fixtures |
| C1/C2 PIT/price integrity / P1 | Pending verification | Pending | — | Preserve historical rows; no forward leakage | Reproduce provider/adjustment collision |
| C SEC timestamp interpretation / P1 | Hypothesis | Pending | — | Requires real filing comparison | Verify before modifying |
| D1/D2 scoring validation + prospective OOS / P1 | Pending verification | Pending | — | Costs/survivorship/OOS; no invented probability | Engine-level prospective tests |
| E freshness / P1 | Verified | Implemented and deployed | #26 | Reader-clock stale indicator after 45 minutes | Preserve |
| E accessibility/UI semantics / P2 | Pending verification | Pending | — | Avoid frameworks/unsupported evidence wording | Focused tests only |
| D1 unchanged issuer writes / P0 | Special production-baseline branch | Open; deployment independent of merge | #18 | Never blindly merge emergency branch | Explicit baseline/coverage review |
| Incident recovery | Completed per human; permanently claimed, disabled | Closed execution path | #20/#22/#23/#24 | Never retry, reset claim, extend window or replay | No action |

## R0a bounded pass and stop point

The relay checks the exact main run/attempt/workflow, a **successful completed
monitor job**, skipped incident eligibility, exact uploaded artifact ID, download,
manifest/source SHA, DB SHA256 and SQLite integrity/foreign keys. Only then does it
POST to the fixed existing Edge origin using `EDGE_SYNC_TOKEN`. Redirects, unknown
responses, failures and timeouts fail without retry. No provider payload is logged.
The singleton stores a monotonic, replay-idempotent receipt independently of D1.

Both Actions `vars.EDGE_CHECKPOINT_FEEDBACK_ENABLED` and Worker environment
`EDGE_CHECKPOINT_FEEDBACK_ENABLED` must equal `1`; missing means disabled. Neither
has been set. Secret injection occurs only in the final verification/send step,
after checkout/setup. The existing workflow concurrency remains; when eventually
enabled, this post-monitor job holds that lock up to three additional minutes.

This first slice **does not remove #25's GET owner checks or enable heartbeat**.
The alarm's heartbeat path also now requires the separate explicit Worker flag
`EDGE_MONITOR_HEARTBEAT_ENABLED=1`; absent means source polling only. No flag has
been changed. Feedback-only deployment must not enable that flag.
It does not claim that a completed POST is a checkpoint, nor that an old receipt
proves current artifact availability. Restore/Pages still independently download
and validate authoritative artifacts. Shared-secret holders remain a trusted
control-plane boundary; the receipt is not a cryptographic GitHub attestation.

Remaining blocker for a GET-independent R0a: durable dispatch challenge/run
correlation, queued/processing/failed/overdue transitions and safe reconciliation
of missing completion feedback. Releasing an ambiguous owner on a timer admits
duplicate queued dispatches; retaining it forever blocks unattended resumption.
No finite lease or blind timer was added. Identity #21 is merged. Work proceeds separately on A1/A4 after Pages #29;
R0a remains HOLD. This is a staged implementation, not rollout approval.

Rollback after a future separately authorized rollout: disable both feedback
flags explicitly (Worker `keep_vars=true`), restore reviewed Worker version/code,
retain the durable receipt maximum, artifacts, ACKs and all historical identities.
No receipt reset, recovery attempt or notification replay is a rollback action.
