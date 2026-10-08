# Hot Edge v3 operator and architecture notes

The Cloudflare Durable Object is the rapid detection clock. Its alarm defaults to
15 seconds (`EDGE_INTERVAL_SECONDS`, minimum 10). The two-minute cron only re-arms
the alarm. The edge checks SEC Latest Filings 8-K and 6-K Atom feeds plus two wire
RSS feeds. It stores issuer identities and Stage-0 events in D1. A matched SEC
filing is persisted before text classification; EX-99 is preferred when available.
An 8-K title alone does not imply a positive or negative outcome. Stage-0 NTFY
is sent only for deterministic material text; the event ID is included in the
message. Failed delivery or enrichment dispatch is retried on later alarms.

Python hot lane remains authoritative for source reading, enrichment, provenance,
outbox delivery, and the only product scoring path (`engine_v2.score_event`).
GitHub Actions receives optional ticker, CIK, accession, and source URL from the
edge and checks that issuer before the general priority cohort. Its schedule is
reconciliation and fallback, not the breaking-event clock. Research validation
and Pages remain separate consumers. CT.gov stays discovery-only; readout,
PDUFA, and AdCom remain distinct. RUN-UP and HOLD gates are unchanged.

The Python `hot_latency` payload reports persisted publication-to-first-seen,
publication-to-queue, publication-to-send, first-seen-to-send, and queue-to-send
statistics by source. Missing timestamps yield `N/A`, not zero. The Edge D1
`edge_events` table separately records SEC acceptance or wire publication and
Stage-0 first-seen times. Measure the <30s p50 / <60s p95 target from production
rows after activation; no latency target is claimed from fixture tests.

## Activation

1. Apply `cloudflare/migrations/0002_hot_edge.sql` to the existing `mozes-alerts`
   D1 database, then deploy the Worker with the `HOT_EDGE` SQLite Durable Object
   binding. Keep the existing `ALERTS_DB` and `/alerts` feed migration.
2. Configure Worker secrets `EDGE_SYNC_TOKEN`, `GITHUB_TOKEN`, `NTFY_URL`, and
   an identifying `SEC_USER_AGENT`. Configure GitHub secret `EDGE_SYNC_TOKEN`
   with the same random value and GitHub variable `MOZES_EDGE_SYNC_URL` pointing
   to the Worker `/edge/sync` URL. The sync endpoint accepts a bounded full
   issuer snapshot and exposes no diagnostics or tokens.
3. Confirm `/edge/health` reports per-source success/error times, consecutive
   errors, last and next alarm times. An unauthenticated POST to `/edge/sync`
   must return 401. Verify a known filing and a wire item in D1 before enabling
   Stage-0 NTFY; check the final Python alert has a distinct Stage-1 identity.
4. Use production D1 and Python latency ledgers to measure actual p50/p95.
   SEC and FDA live reachability are release checks; offline fixtures do not
   replace them.

The D1 migration, Worker deployment, secrets, and live notification test affect
the Cloudflare/GitHub account and require the account owner's authorization.
The code branch alone does not activate the edge.

## Rollback

Redeploy the previous Worker release so its cron again dispatches the existing
GitHub workflow. Keep D1 tables for audit; do not delete Stage-0 events. Disable
the new sync step or remove `MOZES_EDGE_SYNC_URL` after rollback. The Python
reconciliation workflow and research engine remain available throughout.
