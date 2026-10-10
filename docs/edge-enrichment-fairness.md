# Edge enrichment fairness — review and deployment boundary

Audit: 2026-10-10 UTC; code baseline `38faf534d13de2ed520ca9340bbae7f468cdf62e`.
No production Worker, schema, interval, event, receipt or source configuration was changed.

## Proven failure and bounded fix

The existing oldest-first LIMIT 3 selection continually reclaimed GKOS/VIR/ALMS when their 900-second ACK deadlines expired. They successfully dispatched to Actions but exact-source processing failed, so no completion ACK existed. Younger GKOS/SGMT/ATOS remained at zero attempts. The isolated six-event production-identity regression fails on baseline (only three unique events serviced) and passes after the fix (six serviced in two eligible batches).

A Durable Object scheduling cursor walks `(first_seen_at,event_id)` with at most two LIMIT queries and three selected rows. Each round freezes its arrival cutoff, preventing a continuously growing tail from starving old retries. At the end, selection wraps and admits newer arrivals. Persist the hint before dispatch; cursor-storage failure stops before external writes. D1 eligibility, retry deadlines, ACKs and receipts remain authoritative. Empty queues clear the hint. Instance-local drains coalesce, and a single atomic conditional D1 UPDATE rechecks eligibility, per-event deadline and the global three-active cap before dispatch.

For a finite due round of N rows, service takes at most ceil(N/available capacity) successful drain batches, plus existing ACK/deadline waits. Six pending rows with three slots receive service in two eligible batches (about 16 minutes between batches under a 120-second clock/900-second deadline). No bound promises a successful publisher fetch, overcomes a future cooldown or an unavailable dispatcher, or guarantees fixed latency under an unbounded backlog. Fair scheduling does not bypass a source restriction.

Regression coverage includes repeated blocked-source rotation, restart, continuous arrivals, tied timestamps, future deadlines, overlapping disjoint selections, dispatch failures/backoff, material downgrade, failed cursor persistence, retained identities and idempotent completion ACKs. Existing monitor tests continue to cover primary-source Retry-After/403 and ACK-after-durable-upload. No external notification channel is enabled or modified.

## Measured D1 cost

`node cloudflare/benchmark_fairness.mjs` runs 32 actual local workerd ticks and scales to 720/day at 120 seconds. Before is the pinned baseline, same seeded state; no source ACKs simulate blocked publishers. This measures selection/claim queries only, not live account usage or the entire Worker. DO cursor writes are outside D1.

| Schema / fixture | Baseline reads/day | Fair selection reads/day | Added D1 writes/day |
| --- | ---: | ---: | ---: |
| 0001–0004; 21 retained + 6 pending (27 total) | 25,020 | 7,155 | 0 |
| 0001–0005 local only; 10,000 retained + 6 pending | 7,200 | 4,028 | 0 |

The current-size legacy fixture saves 17,865 reads/day (~0.3573% of the 5M free read budget), rather than adding reads. Matrices with 0/6/300/3,000 pending and 21/10,000 retained rows reject >1% additional daily free reads or >10% scan amplification (100-row measurement tolerance). After 0005, a tail-empty wrap reads 17 rows with six pending, independent of completed history. Before 0005, existing indexes still scan retained history; a 10,000-row legacy wrap can read ~20,032 rows. This repair reduces legacy drain frequency but does not claim to remove that pre-existing scale risk. Migration 0005 was used only in isolated local fixtures.

## Current production evidence

Authenticated read-only Actions audit [38037111959](https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/38037111959) refreshed event state without local token access. At 08:13 UTC the six unacknowledged material rows were GKOS older (172 attempts), VIR (166), ALMS (41), and newer GKOS/SGMT/ATOS (0 each). Last observed publisher evidence: Business Wire timeouts for older GKOS/VIR, GlobeNewswire HTTP 403 for ALMS; SGMT has prior timeout evidence. The new three have not been dispatched by this Worker, so prior source symptoms are not fabricated current completions. All six retain their IDs and no completion ACK.

The TENX canonical completion remains `2026-10-09T21:26:35.727Z`, linked to CHG-11e609c8bb36028aaad4edae and its existing sent log receipt. A related GKOS wire receipt exists, but cannot be substituted for the pending canonical event's ACK. Related content must reuse/reconcile stable changes and receipts during real processing.

Automatic runs 38036766520/38036766856/38036767122 and 38037683142/38037683185/38037683324 succeeded. They preserve lineage despite blocked enrichment. Genuine scheduled runs 38001861059, 38016312209 and 38029555075 succeeded; this proves cron execution, not exact 15-minute timing. Snapshot evidence is time-bound; refresh immediately before an approved rollout.

## Ten-accession read-only review

Official submissions and primary documents were read at no more than one request/second, with bounded responses; all succeeded. Linked PCRX/EPRX official exhibits were also checked. All ten accession-derived Edge IDs are present and classified. The verified monitor checkpoint has no accession-linked change/delivery receipt for these ten. That is not proof that equivalent wire coverage or a material alert is absent. Seven acceptance timestamps overlap the conservative outage window; VTGN/BRTX/PCVX are post-recovery. No filing was ingested or acknowledged.

| Issuer | Official primary evidence | SEC acceptance UTC / timing | Edge state | Recommendation |
| --- | --- | --- | --- | --- |
| ICLR | [0001628280-26-065404](https://www.sec.gov/Archives/edgar/data/1060955/000162828026065404/iconschedulesearningscall6.htm) | 2026-10-08T16:15:30.000Z / outage overlap | retained; material=0 | Earnings-call scheduling; no clinical result. No ingestion recommended. |
| JAZZ | [0001628280-26-065497](https://www.sec.gov/Archives/edgar/data/1232524/000162828026065497/jazz-20261006.htm) | 2026-10-09T17:02:20.000Z / outage overlap | retained; material=0 | Chief commercial officer retirement. Governance; no clinical alert ingestion. |
| PCRX | [0001104659-26-114571](https://www.sec.gov/Archives/edgar/data/1396814/000110465926114571/tm2627240d2_8k.htm) | 2026-10-08T17:24:36.000Z / outage overlap | retained; material=0 | Definitive Viatris cash acquisition ($36.50/share). Corporate materiality merits explicit scope review; retained Edge outside_scope is not proof of immateriality. Selected ingestion only after approval and identity/receipt reconciliation. |
| VTGN | [0001628280-26-065559](https://www.sec.gov/Archives/edgar/data/1411685/000162828026065559/vtgn-20261007.htm) | 2026-10-10T01:15:59.000Z / post-recovery | retained; material=0 | Executive severance plan. Post-recovery filing; no clinical ingestion. |
| ATOS | [0001193125-26-418560](https://www.sec.gov/Archives/edgar/data/1488039/000119312526418560/atos-20261008.htm) | 2026-10-09T16:15:15.000Z / outage overlap | retained; material=1 | Conditional PRV contingent value rights, not an FDA approval. Already pending material Edge event; correlate existing wire CHG-297b80cc6dd08a1875425a2c. Do not create a second backfill event. |
| BRTX | [0001213900-26-108467](https://www.sec.gov/Archives/edgar/data/1505497/000121390026108467/ea0305784-8k_biore.htm) | 2026-10-10T00:05:40.000Z / post-recovery | retained; material=0 | Independent director appointment. Post-recovery governance; no clinical ingestion. |
| EPRX | [0001171843-26-006551](https://www.sec.gov/Archives/edgar/data/1581178/000117184326006551/f6k_100926.htm) | 2026-10-09T16:27:27.000Z / outage overlap | retained; material=0 | EP104GI RESOLVE conference presentation (52-week symptoms/histology). Review new clinical facts against existing December catalyst CHG-28dc73e2db8619865dc62e8c; selected assessment, not automatic duplicate catalyst alert. |
| PCVX | [0001193125-26-419174](https://www.sec.gov/Archives/edgar/data/1649094/000119312526419174/d150490d8k.htm) | 2026-10-10T00:42:27.000Z / post-recovery | retained; material=0 | Convertible debt/equity financing. Post-recovery and financially material; explicit financial-scope approval before ingestion. |
| CMND | [0001213900-26-107727](https://www.sec.gov/Archives/edgar/data/1892500/000121390026107727/ea0308056-6k_clearmind.htm) | 2026-10-08T15:55:40.000Z / outage overlap | retained; material=0 | Japanese patent publication; not efficacy or regulatory approval. No clinical ingestion. |
| NSRX | [0001493152-26-046340](https://www.sec.gov/Archives/edgar/data/2029039/000149315226046340/form6-k.htm) | 2026-10-09T00:30:03.000Z / outage overlap | retained; material=0 | Shareholder meeting results. Governance; no clinical ingestion. |

PCRX/EPRX are the narrow follow-up review candidates, not an approved replay list. For any separately approved ingestion: refresh canonical Edge state and exact-source content; match existing event/change IDs and receipt keys; compare material new facts against existing wire/SEC history; refuse ambiguous delivery; ingest one reviewed accession at a time through existing idempotent processing; upload a durable checkpoint before ACK; verify published receipt/feed and duplicates before proceeding. ATOS belongs to the existing backlog, not a new backfill. No broad ten-filing replay is warranted.

## Browser-only notifications

The production site and alert feed were inspected while open. Existing alerts render in the Alerts page, and source text specifies 30-second feed polling. Full browser regression tests exercise actual `alerts_ui.js`: a new feed alert causes an in-site popup, navigation to Alerts, and no repeat after poll/refresh. Popup implementation uses DOM UI and local seen IDs, not native Notification permission. No email/NTFY/Telegram is activated. No genuinely new production alert arrived during the observation so far: live new-alert-to-popup delivery remains unverified; fixture success is not reported as production delivery. Do not manufacture an alert or dispatch a manual producer to complete this check.

## Separately approved code-only Worker deployment

Current main contains Stage B code requiring 0005. **Do not deploy the complete PR/main Worker without the separately approved Stage B rollout.** A production-compatible fairness-only candidate is supplied in `docs/d1/queue-fairness-stage-a.patch`, applied after the existing history-preserving `docs/d1/rollback-stage-a.patch` to Stage A `19b75393b1dc3fe504a26fb7d770e2457e86cabf`. The Python regression builds this exact candidate and runs its Worker/contract/fairness suites with migrations 0001–0004 only. Wrangler 4.149.0 dry-run passed with explicit interval 120, keep-vars, HotEdge and mozes-alerts bindings.

After separate approval:

1. Recheck current deployment/version, effective interval 120, all secret names/bindings, current migration history (0001–0004 only), health and latest durable monitor artifact. Confirm the documented Stage A base matches production behavior; record immutable previous Worker version for rollback. Stop on any mismatch.
2. Create an isolated detached Stage A checkout. Apply/check history-preservation patch, then fairness patch. Run reviewed legacy regression and pinned Wrangler dry-run with `--keep-vars --var EDGE_INTERVAL_SECONDS:120`; inspect bindings and output. Preserve protected evidence outside Git.
3. Deploy **only that reviewed code-only candidate** with the same explicit interval/keep-vars settings. No D1 migration, database restoration, secrets/channel changes or Stage B bundle.
4. Observe ordinary automatic cycles: six-event rotation, max three active, primary-source cooldowns, canonical ACK only after successful processing/upload, stable identities/receipts and no duplicate notifications. A 403/timeout may legitimately leave a row pending. Do not manually replay events.
5. If regression occurs, roll back to the recorded prior Worker version (no database/cursor/receipt reset); verify interval 120, history retention, health and automatic processing. Preserve logs/checkpoints and investigate before retrying deployment.

Code-only rollout recommendation: conditional GO after PR CI/review and the immediate deployment-baseline checks above; no deployment authorization is implied. Selected SEC ingestion: NO-GO until the narrow scope/materiality and identity/receipt review receives separate approval. Stage B: NO-GO pending production fairness evidence, remaining source/coverage decisions and the existing backup/migration/environment rollout gates.
