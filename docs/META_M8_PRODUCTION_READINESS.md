# META-M8 production readiness: evidence and owner decisions

Sequential review baseline: main `2569ad87e18d66d162ccc9e22ef970800e63d65e`,
the owner-approved PR48 squash merge of reviewed HEAD
`c4786586e0598e2f8df10a355ec73654aaae980f`. This is a transition plan,
not permission or an executable rollout. PR35 remains HOLD; PR46 is excluded.

## What exists, what is merged, what is active

| Component | Implemented/tested | Merged | Production use |
| --- | --- | --- | --- |
| Official pinned Python registry source (PR56) | Image and full CI | Yes, main above | Build source only; application behavior unchanged |
| Immutable offline adapters, separate staging DB, selection and frozen manifests (PR48) | Focused and full exact-head CI recorded in PR | Yes, squash `2569ad87e18d66d162ccc9e22ef970800e63d65e`; tree matches reviewed result | Inactive; no live caller or production schema hook |
| Read-only offline shadow comparison and CLI (dependent shadow PR) | Synthetic regression/CLI tests and exact-head CI recorded in PR | No, Draft | Inactive; no workflow invokes it on live data |
| Both legacy price writers and existing consumers | Existing production path | Already in main | Still active; the overwrite collision boundary remains unresolved |
| Durable staging storage, producer/consumer switch, historical migration | Not implemented/authorized by these drafts | No | HOLD |

No library control is configured by these changes. STAGING, SELECTION and SHADOW
require exact opt-in and default OFF; no repository variable is changed. Enabling
a library control alone does not route existing writers or consumers.

## Ordered readiness gates

Every future phase needs a reviewed evidence bundle tied to code SHA, input
hashes and source checkpoints. A pending decision is HOLD, never an inferred PASS.

| Gate and decision owner | Concrete implementation/evidence required | Pass condition / stop condition |
| --- | --- | --- |
| Durable storage and serialized ownership — repository owner | Name the staging volume/path, writer service, lock/lease and failure ownership; prove one serialized producer under concurrency/restart. Keep staging separate from the monitor DB. | Exactly one authorized writer; no cross-store production write without the approved ownership protocol. Currently HOLD. |
| Backup, restore, retention and quota — operations owner | Define an atomic pair/checkpoint manifest with both DB hashes, producer run/code, source evidence and immutable attachment references; provision a byte quota and export/retention policy. Test restore into isolated storage and compare all run/observation/manifest identities. | Zero lost/changed observations or manifests and verified paired restore; disk-full/partial-checkpoint failures refuse new writes. Library row budgets alone are insufficient. Currently HOLD. |
| Instrument/provider policy — data owner | Review namespaced instrument mapping, ticker changes, currencies, exchange/timezone and explicit selected price/volume fields. Pin provider methodology and adjustment-policy evidence; separate original and corrected vintages. | Zero unknown/ambiguous basis or instrument mappings in selected data. Do not treat a field called close as proof of raw prices. Currently HOLD for live sources. |
| Source evidence/availability — data owner | Retain approved response bytes outside public exports, SHA256, declarations and independent first-availability evidence. Capture time remains distinct from availability; protect credentials. | No source after capture/cutoff, no unproven historical availability, exact response hashes; caller attestations alone do not establish historical truth. Currently HOLD for live integration. |
| Session coverage and XBI — data/research owner | Approve calendar/version and ordered session list, including holidays/early closes; pair stock/XBI currency, basis and cutoff while preserving independent provider/vintage IDs. | Zero missing/extra/duplicate sessions; no interpolation, splicing or provider fallback. Reviewed coverage universe and interval must be named before a live campaign. Currently HOLD. |
| Producer-only observation — engineering + explicit owner approval | Add raw-response capture before normalization for both `db.store_prices` and `ingest.prices.store` call routes, but retain old writers/consumers during an approved observation phase. Add conflict quarantine and idempotent retry handling under the same serialized owner. | Old data/checkpoint/attachment bytes unchanged; no replacement/backfill or inferred old provenance. No live observation in the current drafts. |
| Quantitative offline shadow — engineering/research | Run pinned stock/XBI windows against immutable offline checkpoint copies; save comparator JSON, input/selection/response hashes and independent expected values. Cover splits, dividends, provider corrections, duplicate/missing dates, independent vintages, mismatched sessions, retries and conflicts. | Zero unexplained close/volume differences, zero missing/extra sessions, zero hash/identity failures; 100% of expected refusal scenarios refuse. Exact numerical comparison is the default; any rounding tolerance requires explicit policy approval. Passing numbers never certify legacy provenance. |
| Consumer transition — engineering + explicit owner approval | Inventory `db.load_prices`, direct `prices` reads, historical frozen readers, export/scoring/monitor callers. Give each new consumer explicit instrument/provider/basis/vintage/calendar/cutoff inputs and missing-data behavior; preserve existing attachment namespace. | All affected consumers tested against old/new storage states with no silent fallback and no regeneration of frozen records. Not implemented or authorized. |
| Retire BOTH overwrite paths — owner approval after consumer gate | Remove or freeze direct legacy replacement access, including CLI/live/historical/pipeline routes and the delegating ingest writer. Never project selected staging data back through `INSERT OR REPLACE`. | No remaining reachable replacement route for new observations; legacy history retained. No writer retirement in these drafts. |
| Rollback and operational isolation — operations + owner | Before each approved phase, rehearse stopping new callers and returning consumers to the reviewed prior code while retaining every staged observation/manifest/checkpoint. Compare alert receipts, delivery deduplication, ordinary monitor continuity and restore ownership. | No observation deletion, notification replay, ledger fabrication or historical attachment rewrite; no new scoring/alert/publication/investment gate. Separate rollout approval is still required. |

## Evidence bundle and approval sequence

1. PR48 is merged as dormant code only. It does not authorize any phase above.
2. Review PR57's shadow-only diff against the post-PR48 main. Its normal merge
   preserves reviewed history and staging prerequisites. Verify the merge tree
   and repeat exact-head hosted CI before a separate owner merge approval;
   PR57 remains Draft and must not be merged under the PR48 authorization.
3. For each offline scenario retain code SHA, run/declaration/response hashes,
   snapshot DB hashes, explicit session/cutoff requests, selected observation/run
   identities, independently expected field values and the full comparison/refusal
   report. Exact-head CI must include Python, frontend, Worker/D1 and image/package
   checks. Synthetic agreement is not measured investment performance.
4. Resolve the owner decisions in gates 1–5 before proposing any live observation
   work. A future durable pairing/producer implementation needs its own narrow PR,
   offline failure/restart tests and explicit activation approval. No deployment,
   backfill, capture, migration or consumer cutover is scheduled by this document.
5. Scoring, public exports/Pages, alerts and investment eligibility retain existing
   behavior throughout these drafts. Any future change to them is independently
   reviewed and authorized, never an incidental consequence of a price migration.

The prospective one-shot pilot remains closed and OFF. This integration neither
renews its authorization nor initiates capture.
