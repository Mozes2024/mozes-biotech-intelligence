# A5 public feed dependency (review only)

## Cross-version gate

### P0 cache isolation and static prerequisite

At 85caa58, five real HotEdge.fetch regressions fail: full-first and legacy-first
cached responses, both overlapping request orders, and per-variant POST reads.
Both alertCache and alertLoading now have exactly two normalized capability
keys; unknown clients share legacy. Each variant retains its five-second TTL
and same-capability request coalescing. Successful POST invalidates all variants;
generation checks prevent stale pending reads from warming either new cache.

An old initialized script also reproduces unwanted Notification when Worker
fetch fails and currently deployed alerts.json contains a new list-only row.
Therefore #34 includes the compatibility-only Python browser projection and
offline export previously placed in #35, without A5 classification changes.
The unversioned static/API views fail closed, and alerts-evidence-v1.json retains
the complete list. Existing outbox rows and receipts are never changed.

Required rollout order: separately approve merge #34; allow normal Pages export
and verify the protected static fallback plus schema-2 checkpoint receipt BEFORE
authorizing Worker #34 activation. Then verify real Durable Object requests in
both orders/concurrently and during a Worker outage. Only after these gates may
#35 producer/frontend/Stage-0 be separately approved. No manual refresh is a
safety requirement. Legacy metadata cannot prove urgency, so browser interrupts
remain temporarily suppressed until A5 supplies explicit verified metadata.
The tested new urgent events still notify once; full-list clients retain all rows.
Do not roll back the protected fallback or compatible Worker while A5 is active.

An initialized pre-A5 tab notifies on every unseen change_id, even when
popup_eligible is false. The captured main 6f939b8 script reproduces both popup
and system notification against the serialized A5 feed in the contract tests.
GET without client=alert-evidence-v1 now exposes only explicitly eligible rows.
The complete stored snapshot, IDs, sequence, revision and receipt remain intact;
capability-aware clients retain all list rows. Unknown clients fail closed.
During Worker-first rollout, legacy producer metadata cannot prove eligibility,
so its browser interrupts are temporarily suppressed until A5 producer activation.
The new frontend must use the capability parameter and an evidence-aware static
fallback; the unversioned static/API routes must also be interrupt-only.
No manual refresh is required to protect old tabs. Their list is intentionally
limited to urgent rows until refreshed; new clients retain the complete list.
Displayed quotation omission is independent of source verification: oversized
quotes are omitted with a limitation; missing verification still fails closed.

The current public feed drops semantic family and popup eligibility. A browser
cannot safely infer them from a priority or a headline. This isolated change
preserves bounded semantic metadata without changing stored identity, polarity,
priority, delivery or sequence/receipt ordering. The endpoint, authorization,
schema-1 envelope, D1 schema and writer ownership remain unchanged.

Popup eligibility requires explicit verified issuer, confirmed outcome, no review
flag, `alert-evidence-v1`, a supported family/outcome pair, source-backed evidence
basis and P1/P2. Missing or malformed metadata fails closed for interrupts while
retaining the row in the list. Existing historical ATOS fields are not corrected.

Source-rules-v2 explanations are accepted. Up to three complete bounded quotations
retain their exact text, source hash and offsets. An oversized quotation is omitted
with a limitation, never silently sliced away from its condition or negation.

Eleven executed serialization tests cover metadata survival, legacy/incomplete
rows, private-field removal, quotation provenance and unsafe excerpt omission.
They run in CI alongside the existing HTTP/D1 feed tests. Full exact-head CI is
reported in the PR. This PR does not fix Stage-0 classification or enable A5 itself.

Deployment dependency: the separate A5 implementation must not be activated with
an older Worker that strips its metadata. Review/merge and Worker deployment each
require separate authorization. Upgrade and verify the feed contract before
publishing the A5 consumer, then deploy any A5 Stage-0 changes from the reviewed
combined revision. No Worker deployment, D1 write/migration, source re-ingestion,
ATOS correction, ACK/reset or notification replay was performed here.
