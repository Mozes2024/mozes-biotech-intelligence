# Pages starvation after monitor recovery

Failed runs 39402359125, 39410024253, 39411904492 and 39423501327 all exhausted
the 300-second restore budget while serialized producers were still active.
Three dispatches can occupy the writer queue longer than five minutes. Merely
retrying the same cross-workflow race cannot guarantee a publication opportunity.
Another confirmed defect: completed/skipped Pages runs triggered by PR CI restart
the two-minute publication-dispatch throttle despite making no publication request.
Ignore those terminal no-op runs for throttling and last-attempt reporting; live
runs and genuine failed requests retain the existing coalescing/backoff rules.

Proposed fix: put Pages under the existing `mozes-hot-monitor` workflow queue,
with cancellation disabled and `queue: max`, through build, deployment and receipt.
Pages still reads the newest verified immutable monitor artifact and never opens
the authoritative database for writing. A queued producer is ignored only by the
trusted main Pages context holding that same lock, with no artifact and no job or
step execution. Any active writer still blocks. Hashes, provenance, complete
inventories, chronology, current code, reselection and no-downgrade checks remain.

The existing publication dispatcher coalesces requests while Pages is pending or
running. No new scheduler is added. Tradeoff: Pages takes a bounded turn in the
writer queue, so monitoring may wait for the existing eight-minute build and
five-minute deployment limits. The 45-minute age guard is unchanged.

The reader also reports checkpoint-to-export lag and warns after a 20-minute
checkpoint-age target (the intended 15-minute fallback cadence plus five minutes
of publication headroom). That target is an operational warning, not a promise
that GitHub's best-effort scheduler meets an SLA. The hard 45-minute validity
guard remains unchanged. A newly generated HTML/JSON file cannot make a
40-minute checkpoint appear timely. Lag is measured only for the published
checkpoint; the browser does not claim to know a newer unpublished artifact.

Deployment gate: review exact-head full CI, approve this production coordination
change, then merge and observe real monitor and Pages cycles. Do not count the
unit tests as proof of production queue behavior. If the provider shows job
execution in a supposedly waiting producer, fail closed and investigate.
