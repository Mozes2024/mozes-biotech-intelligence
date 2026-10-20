# Preventive idle-monitor watchdog — inactive until reviewed rollout

The current Worker requests generic reconciliation only when a source gap is
present. Quiet sources therefore leave monitor continuity dependent on Actions
scheduling or event enrichment. The October 19 observed scheduling gap exceeded
the 45-minute restore safeguard; the provider's internal scheduling cause is unknown.

This adds an opt-in liveness hint, `EDGE_MONITOR_WATCHDOG_ENABLED=1`, to the same
Durable Object alarm. No configuration is changed by this PR. With no source gap,
a feed generation age of 20–40 minutes requests the existing bounded priority
workflow. One durable 15-minute throttle applies; its timestamp is written before
remote I/O so an ambiguous acceptance cannot immediately create another request.
Distinct per-event dispatch and fair delivery are unchanged.

Feed age is not checkpoint proof. The requested workflow still independently
checks the newest artifact, producer provenance, rewind bound and all receipt
gates. An old (>40-minute), absent or future hint never triggers idle recovery.
There is no source-run override, checksum exemption, old-run rerun, new sender,
state reset, notification replay or synthesized source timestamp. Existing
source-gap reconciliation remains unchanged. Health reports only an unverified
request/throttle or recovery requirement, never a successful monitor cycle.

At the unchanged 120-second alarm interval, opt-in adds at most 720 one-row feed
lookups/day; disabled mode adds none. Automatic requests are at most four/hour,
and only while the hint lies in the bounded window. Requests are serialized by
the existing monitor workflow lock. Dispatch is not exactly-once delivery and
cannot guarantee GitHub scheduling availability.

Rollout needs review of the exact Worker bundle, D1 budget and current state,
then an explicitly scoped deployment/configuration decision. First recover the
current stale lineage through the separate evidence and authorization gate.
Do not enable this flag as a workaround for stale recovery. Verify three genuine
scheduled/automatic producer cycles and publication receipts after rollout.
