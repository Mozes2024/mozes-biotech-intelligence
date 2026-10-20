# Code-only stabilization on the production Stage A schema

Production was read-only verified with migrations 0001–0004, no `source_item_id`
and no Stage B delivery index. Do not deploy main's Stage B entrypoint or run
0005. `scripts/build_stage_a_stabilized.py NEW_DIRECTORY` builds from immutable
`19b75393b1dc3fe504a26fb7d770e2457e86cabf`, then applies the history-preserving
rollback patch, fairness patch and stabilization patch in that order. The output
is an isolated deployable checkout; the builder never uploads or migrates.

The final patch carries #69's fresh-event reserve (including the single-free-slot
cursor fix) and #70's explicit, nonretrying size-limit block. It adds the approved
forward-only fence: both first observation and official publication/SEC acceptance
must be at or after the persisted deployment variable `EDGE_FORWARD_ONLY_FROM`.
Missing/invalid activation evidence fails closed. Historical records and ACKs stay
readable and unchanged. Unacknowledged historical `/edge/event` requests return
409 plus their authentic record, preventing previously queued jobs from replaying
them. The candidate endpoint offers only forward work, without deleting old rows.

Forward enrichment has at most five attempts, under the existing atomic cap of
three and existing completion deadlines. After that budget expires, work stays
pending for manual resolution. Health distinguishes eligible work, active claims,
manual review and blocked analysis, and remains PARTIAL for unresolved gaps.
There is no success, delivery or ACK manufactured for these states.

One durable, bounded activation review re-fetches up to three old generic SEC
analysis failures, once, to distinguish a genuine size-limit block from a
retryable transport error. It never infers success or blockage from attempt count;
ordinary backoff resumes for HTTP/network failures, including after restart.

Tests build the exact candidate with only 0001–0004, exercise real SQLite claims,
fresh admission, old retries, the single-slot boundary, restart, retained RAPP,
historical/unknown timestamps, candidate filtering, exhausted retries and MESO.
The earlier history/fairness candidate's complete legacy suite also remains tested.
Main's complete Python/frontend/Worker/D1/image CI must pass on this exact commit.

Before the one authorized rollout: preserve the active immutable version ID,
version/runtime/binding metadata, D1 bookmark, schema and bounded table snapshots;
verify no producer is currently processing a source; record authentic history and
receipt counts. Build and dry-run the isolated candidate using pinned Wrangler
4.149.0, `--keep-vars --var EDGE_INTERVAL_SECONDS:120` and the fixed UTC activation
timestamp chosen immediately before deployment. Inspect the DO class/namespace,
D1 binding, compatibility date and existing cron. Do not add migration tags or
change secrets/channels. Deploy that same bundle once. Verify active version,
variable, schema, history and ordinary monitoring/Pages cycles afterwards.

Rollback readiness is the recorded immutable production version, without a D1
restore or cursor reset. A rollback is a separate response to a concrete failure,
not a blind second deployment. Retain all evidence and do not retry a rollout
with ambiguous outcome. A new genuinely qualifying production event is required
to claim live fresh-event latency; offline fixtures are not production alerts.
