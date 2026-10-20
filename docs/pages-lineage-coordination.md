# Pages starvation after monitor recovery

Failed runs 39402359125, 39410024253, 39411904492 and 39423501327 all exhausted
the 300-second restore budget while serialized producers were still active.
Three dispatches can occupy the writer queue longer than five minutes. Merely
retrying the same cross-workflow race cannot guarantee a publication opportunity.

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

Deployment gate: review exact-head full CI, approve this production coordination
change, then merge and observe real monitor and Pages cycles. Do not count the
unit tests as proof of production queue behavior. If the provider shows job
execution in a supposedly waiting producer, fail closed and investigate.
