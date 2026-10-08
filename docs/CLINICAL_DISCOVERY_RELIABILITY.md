# Clinical discovery and future catalyst reliability

## Status and scope

Base: main `1b437eb8058be702c7478648abaf40780f20510d`; no open PRs at intake.
Branch: `codex/clinical-catalyst-discovery`. This change is prepared for review.
No production deployment, production database migration or secret change was performed for this task.
Existing Hot Edge v3.1 work (PRs 5, 6, 7) is retained. Existing scoring, RAG and tradability gates are unchanged.

## Incident evidence and root cause

The [official October 5 Vir release](https://investors.vir.bio/news/news-details/2026/Vir-Biotechnology-to-Present-Expanded-Phase-2-SOLSTICE-Results-for-Elebsiran-and-Tobevibart-in-Hepatitis-Delta-at-AASLDs-The-Liver-Meeting-2026/default.aspx)
announces expanded Phase 2 SOLSTICE data presentations at AASLD, with presentation dates of November 8.
This is an upcoming clinical milestone; the announcement alone does not establish positive new results.

Read-only production evidence from durable monitor artifact run `37774631796`:

* The exact SOLSTICE headline **was ingested**, as secondary `news_signal`
  `CHG-e564108c0a39242732243d70`, detected October 6 at `13:25:21.053212+00:00`.
* It was assigned to **ALNY**, not VIR. Its publisher metadata identifies Business Wire and its URL is Google RSS.
  Its recorded publication timestamp is `2026-10-05T20:05:00+00:00`.
* There is no alert outbox row for that change. There is no archived SOLSTICE article body or Vir IR URL.
* A separate SOLSTICE catalyst-evidence hit under ALNY refers to a **January 2026** statement in ALNY's February 10-K;
  it is not the October announcement and cannot prove October catalyst ingestion.
* VIR has an authoritative SEC equity mapping (CIK `1706431`), but was absent from the active SQLite watch universe.
  Edge's synced issuer table contains VIR. No corresponding VIR/SOLSTICE Edge event was found.

Confirmed failure: `poll_news` inherited the issuer from the company search query for every returned headline.
A publisher search can return a partner's announcement; the code did not re-resolve its subject.
Thus the actual secondary path was publication → RSS → wrong issuer ALNY → review news → no alert/no VIR future catalyst.

Additional reproduced code gaps:

* Legacy Python `classify_outcome` treats presentation scheduling as nonmaterial, without an upcoming catalyst path.
* Python wire polling had a 12-hour lookback; a deterministic October 8 replay excludes the October 5 RSS item.
* VIR has no configured IR site, and the hot IR pass previously used priority issuers only.
* The older early wire materiality filter provided no auditable candidate trail.

These gaps are verified in code, but the historical wire/IR fetch receipts are absent.
We cannot claim which primary source was available to the monitor on October 5, or prove a historical wire fetch followed by filtering.
The v3.1 Edge installed on October 8 already marks the supplied VIR title material with unknown direction;
it should not be described as the proven October 5 failure.

## Implementation

Shared deterministic rules separate relevant, material, actionable, catalyst and polarity.
Future data presentations/readouts and regulatory milestones have unknown direction and cannot page as confirmed outcomes.
Important future milestones use `clinical_catalyst_signal` / P2; existing priority configuration still controls delivery.
Actual endpoint successes/failures retain existing urgent-result behavior. Routine conference participation and CFO updates remain suppressed.

Candidates are persisted before final classification. Lifecycle history records
`DISCOVERED → RESOLVED → CLASSIFIED → ENRICHED → PUBLISHED`, or a structured suppression reason.
Unknown symbols are hints. Resolution requires a unique high-confidence SEC equity/company match, with collisions and delisted/acquired/suspended identities rejected.
Generic SEC titles may use the exact verified CIK/accession path. IR titles may use the registered official host.
Search results are reattributed independently of their query; secondary reporting remains investigation evidence and cannot create a verified catalyst by itself.
Previously unresolved candidates are retried after authoritative mappings become available.

Primary/near-primary future announcements create durable catalyst records with source URL, source publication timestamp,
program/type identity, and normalized timing precision. An explicit date wins over a conference year;
Q4 remains October–December, and unknown timing remains unknown. Same program/type/year evidence links to one card;
material timing changes update the card and can create a new P2 decision.
Cards are displayed in the clinical milestone section of the dashboard, with escaped text and HTTPS source links.

Bounds and cost:

* Edge wire feeds remain capped at 30 entries; no per-event AI calls are introduced.
* D1 candidate audit: 14 days / 3,000 rows, pruned once per day.
* SQLite finalized candidate audit: 14 days / 3,000 rows. Catalyst provenance survives candidate pruning.
* Discovery control read returns at most 20 rows; each monitor run reconciles at most 10.
* Unresolved Edge verification retries back off from five minutes to at most one day.
* Python unresolved retries inspect at most eight entries per pass; official IR adds at most eight registry entries per pass, oldest first.
* Candidate summaries are capped at 8 KB, URLs at 2 KB, histories at ten transitions in D1.
* Repeated identical publisher entries do not append candidate history or duplicate notifications.

Existing producer checkpoint/database SHA binding applies to the candidate receipts.
Workflow order is process → monitor/export → checkpoint → immutable artifact upload → ACK.
ACKs are idempotent; changed evidence rejects stale receipts. Failed article analysis remains retryable with `source_unavailable`.
The old Worker's generic route response and HTTP 404 are recognized as `WAITING_DEPLOYMENT`, allowing repository code to merge before Worker activation.

## Changed files

| Area | Files |
|---|---|
| Shared policy / clinical persistence | `mozes/data/clinical_event_rules.json`, `mozes/clinical_events.py`, `mozes/schema.sql`, `cloudflare/src/clinical-events.js` |
| Source ingestion / identity | `mozes/primary_feeds.py`, `mozes/news_signals.py`, `mozes/hot_monitor.py`, `mozes/live_monitor.py`, `mozes/edge_enrichment.py` |
| Queue / private controls | `mozes/clinical_reconcile.py`, `cloudflare/src/candidate-contract.js`, `cloudflare/src/edge-contract.js`, `cloudflare/src/hot-edge.js`, `cloudflare/src/index.js` |
| D1 | `cloudflare/migrations/0004_clinical_candidates.sql` |
| Delivery / UI | `mozes/materiality.py`, `mozes/alert_dispatch.py`, `mozes/payload_v3.py`, `web/live_intelligence_ui.js`, `.github/workflows/lightweight-monitor.yml` |
| Regression tests | `tests/fixtures/clinical_announcements.json`, `tests/test_clinical_candidates.py`, `cloudflare/test_clinical.mjs`, `cloudflare/test_candidates.mjs`, `cloudflare/test_hardening.mjs`, `cloudflare/test_hot_edge.mjs` |

Inspected without changing: `mozes/edge_sync.py` already supplies the watched/active Phase 2/3 universe, capped at 5,000;
`mozes/alert_feed.py` already publishes durable Stage-1 outbox decisions;
`mozes/pipeline_v2c.py` uses the existing payload/export and priority monitoring architecture.

## Tests

The ten requested deterministic fixtures cover VIR, positive Phase 2, negative Phase 3,
quarter-only Phase 3 timing, routine conference, CFO, unknown issuer, identity collision,
SEC/Business Wire/GlobeNewswire duplication, and reclassification after new evidence.
Additional tests cover wrong-query ALNY attribution, RFC publication timestamps, generic SEC CIK binding,
retention with surviving provenance, timing updates, regulatory/enrollment milestones, confirmed outcomes despite future secondary data,
dashboard escaping, private endpoint authentication, queue retry/backoff, idempotent ACK and lifecycle completion.

Run: `python -m pytest -q`; `python -m compileall -q mozes scripts`;
`node cloudflare/test.mjs`; `node cloudflare/test_alert_feed.mjs`; `node cloudflare/test_hot_edge.mjs`;
`node --check web/live_intelligence_ui.js`; `git diff --check`.
Worker packaging is checked with `npx wrangler deploy --dry-run` (no upload or deployment).
CI repeats Python/Node checks on Linux/Python 3.11/Node 24 and builds the Docker image.

## Deployment instructions — authorization required

1. Review and merge this PR after CI is green. Preserve the single monitor-state lineage.
2. Inspect pending D1 migrations. Apply **only the additive 0004 migration** before deploying the new Worker:
   from `cloudflare`, `npx wrangler d1 migrations list mozes-alerts --remote`, then
   `npx wrangler d1 migrations apply mozes-alerts --remote` when the listing confirms the expected pending migration.
   Do not rerun legacy migrations or infer dispatches as completed ACKs.
3. `npx wrangler deploy`. No new secrets, NTFY setup, permissions or Durable Object migration are required.
4. Allow the existing DO alarm cycle to adopt the new version. Run the existing lightweight monitor workflow
   using its current validated artifact lineage; do not use a blank database or a fabricated production event.

### Post-deployment verification

* `/edge/health`: feed health, existing delivery metrics/backlog, and aggregate `clinical_operations`.
* With the existing sync authorization, GET `/edge/investigate?q=VIR`, issuer name, or URL.
  GET `/edge/candidates` verifies unresolved/uncertain queue visibility. Never put tokens in public links or logs.
* Run the monitor and confirm its durable artifact has clinical tables, source provenance, P2 decision,
  correct ticker/CIK, date precision, catalyst card, and producer-bound receipt before D1 ACK.
* Verify a real future clinical event never produces urgent Stage-0 delivery; a real confirmed endpoint event still follows the existing urgent route.
* Replay the official VIR fixture offline against restored read-only evidence plus a local disposable DB.
  Historical RSS availability is not guaranteed: do not claim a live retroactive catch merely because the fixture passes.
* Private SQLite investigation: `python -m mozes.clinical_events VIR` (configured local database).

### Rollback, simply

Rollback means returning the application/Worker to the previously working version if the new behavior causes trouble.
Keep the additive D1 columns/tables and saved artifacts; **do not delete production data or reverse migrations**.
The prior v3.1 Worker version `2f53b81f-910f-4226-b974-26fb9cae33b4` supports the existing DO migration.
Use the verified Worker rollback command/version after approval, and revert the repository PR if needed.
Old code ignores the extra tables; existing CHG/ALT identities and ACK data remain usable.

## Remaining limits

Discovery is bounded, not an exhaustive internet crawler. Missing/expired RSS entries and absent verified IR registries can still leave coverage gaps.
Unresolved identities remain queued, without speculative urgent alerts. Source failures and stale feeds are observable.
Regexes cannot infer arbitrary drug/study synonyms; an unnamed program uses source URL identity to avoid merging unrelated milestones,
which can yield separate cards across differently worded sources. Named program/type/year grouping may need refinement for several distinct same-year conferences.
The shared classifier considers the first 4 KB; dates use at most 8 KB of saved summary. A missing explicit date is retained as uncertain.
Operational counters describe retained audit state, not lifetime cumulative throughput; catalyst/source ledgers are durable.
No production improvement is claimed until approved activation and real post-deployment verification.
