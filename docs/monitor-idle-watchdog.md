# Preventive idle-monitor watchdog

Quiet sources leave monitor continuity dependent on Actions scheduling or event
enrichment. Scheduling gaps previously exceeded the 45-minute restore age gate
and deadlocked ordinary resume even when the newest durable checkpoint was
intact.

Ordinary producer restore now continues from the newest durable checkpoint when
`audit_producers` proves later attempts made no delivery/enrichment writes.
`MAX_REWIND` still refuses rewind to an older artifact. Explicit SHA recovery
remains available for reviewed incidents that need full-history proof.

The Worker opt-in liveness hint is `EDGE_MONITOR_WATCHDOG_ENABLED=1` on the same
Durable Object alarm. With no source gap, a feed generation age of 20 minutes or
more requests the existing bounded priority workflow (including ages beyond
40 minutes). One durable 15-minute throttle applies; its timestamp is written
before remote I/O so an ambiguous acceptance cannot immediately create another
request. Distinct per-event dispatch and fair delivery are unchanged.

Feed age is not checkpoint proof. The requested workflow still independently
checks the newest artifact, producer provenance, rewind bound and evidence
audit. Absent or future hints never trigger idle dispatch. There is no
source-run override, checksum exemption, old-run rerun, new sender, state reset,
notification replay or synthesized source timestamp. Existing source-gap
reconciliation remains unchanged. Health reports only an unverified
request/throttle status, never a successful monitor cycle.

At the unchanged 120-second alarm interval, opt-in adds at most 720 one-row feed
lookups/day; disabled mode adds none. Automatic requests are at most four/hour
while the feed remains stale. Requests are serialized by the existing monitor
workflow lock. Dispatch is not exactly-once delivery and cannot guarantee
GitHub scheduling availability.

Enabling the flag requires an explicitly scoped Worker configuration decision
after the evidence-based resume code is on main. Verify three genuine producer
cycles and publication receipts after rollout.
