# ATOS alert quality repair and production correction boundary

Baseline: `0a2874fb44b63fa4170df857d9e01b68e9e03884`.
This PR changes the Python/site pipeline. It does not change the deployed
Cloudflare Worker, its 120-second interval, D1 schema or migration history.

## Reproduced defect

The exact preserved SEC Exhibit 99.1 for accession `0001193125-26-418560`
has SHA256 `ccaa408dd2adc1964a24a5f4124075e52c5b6989eb3bc5d6d97e2e89f30e6bae`.
The public filing and exhibit are retained as test fixtures with provenance.

The old path was `edge_enrichment.process` → `classify_outcome` →
`classify_clinical` → `build_stage1_payload` → stored outbox payload.
The short negation guard missed the subject between “No” and “approved”.
Clinical classification then treated material regulatory text as a confirmed
development. Priority conflated verified equity identity with monitoring
preference and escalated broad SEC materiality. Explanation extraction started
five tokens before the match, dropping “No”. The browser interrupted for every
new list item, regardless of urgency.

| Field | Existing presentation | Corrected presentation |
| --- | --- | --- |
| Priority | P1 Urgent | P2 Important |
| Polarity | positive | unknown; conditional potential value |
| Category | confirmed development / regulatory | Corporate Action / Conditional CVR |
| Primary-source verification | SEC primary source | unchanged |
| Evidence | excerpt omitted “No” | complete absence sentence, CVR economics and record-date sentences |
| Popup | interruptive popup | list only; correction explicitly ineligible |

The agreement grants one CVR per qualifying share with an October 19, 2026
record date, contingent participation in 25% of qualifying PRV monetization
proceeds and a $50 million aggregate cap. The source denies FDA product approval
and a PRV award; payment is not guaranteed. No price direction is inferred.

## Classification and popup contract

Identity, watchlist membership, materiality, family and urgency are separate.
Confirmed FDA decisions, material clinical holds and confirmed clinical outcomes
remain P1 for uniquely verified active equity issuers, including discoveries
outside `priority_issuers.json`. A watched Nasdaq halt retains its existing
P1 rule. Generic SEC materiality, conditional CVRs, financing and M&A do not
become P1 merely because an issuer is verified or watched.

Clauses retain negation and conditional antecedents across long subjects and
commas. Independent clauses/sentences allow genuine decisions elsewhere in a
document. Regulatory designations, submission and review acceptance are distinct
from approval. Bare “FDA approval” is not proof of a completed approval. Explicit
absence is not rejection. Historical/prospective wording fails conservatively.
Clinical outcome urgency requires a clinical result assertion, not just a
Phase 3 reference near review acceptance. Enriched Edge bodies govern stale
feed headlines. Existing event IDs and acknowledgement logic are unchanged.

The feed exposes `event_family`, `event_outcome`, `issuer_verified` and
`popup_eligible` as additive JSON fields (schema 1 remains compatible). Popups
require P1, or P2 material clinical outcomes / watched urgent developments,
plus primary-source verification or verified issuer identity on a registered
primary source class. An investigation-only feed retains that verification
label even when its issuer identity is verified; it is not promoted to a
full-document verification. Conditional corporate P2 and routine P3 items
remain in the list but do not interrupt. Correction entries always suppress
popups. Older feeds without eligibility metadata fail closed for popups.
Seen-state still keys on `change_id`, including non-popup list items.
Navigation, polling, cross-tab locking and reload deduplication are preserved.
No notification channel or permission is enabled by this PR.

Evidence quotes contain whole sentences, with a 100-word/800-character limit.
Unsafe sentences are omitted with a visible limitation. Raw primary archives
and hashes remain intact. Optional free-form AI paraphrasing is withheld with
`ai_status=evidence_guard`: numbers and evidence IDs alone cannot prove that
a translated paraphrase preserves negation. Deterministic source wording works
regardless of AI configuration.

## Existing alert correction — separate approval required

**Do not execute a production write or dispatch a maintenance/recovery workflow
under this PR's implementation authorization.** The following is the proposed
controlled procedure for separate review/approval, not an executed rollout.

Canonical identities:

- Change: `CHG-01557485de654f94415ef482`
- Edge: `EDGE-d221aba82c9b4438bffd21e4`
- Outbox: `ALT-c9bf615d440a11fa9c838fb6`
- Original processing run: `38043306176`
- Original log receipt: `2026-10-10T10:01:18.777381+00:00`
- Original Edge ACK: `2026-10-10T10:02:18.097Z`

1. Restore the **newest** successful durable checkpoint through the established
   lineage gates. Never restore the earlier ATOS checkpoint over newer state.
   Record artifact availability, manifest/checkpoint SHA256, producer audit,
   published receipt/ACK evidence and absence of ambiguous writes.
2. Use the existing authenticated read-only Edge interface inside Actions to
   confirm the canonical ATOS ACK, change link and source hash. Do not query or
   mutate Cloudflare admin D1. Reject conflicting delivery/completion evidence.
3. Acquire the existing `mozes-hot-monitor` concurrency group with
   `cancel-in-progress: false`. Revalidate the newest producer under that lock;
   reject any competing running producer. Keep automatic scheduling active.
4. Preserve the complete checkpoint, manifest, source archive and audit evidence
   in protected backup outside Git. Compute the current checkpoint SHA256.
5. Generate a read-only plan on a disposable restored copy:

   ```sh
   python -m mozes.alert_correction --db /protected/staged/mozes-live.db > /protected/atos-plan.json
   ```

   Review the source checksum, original job pointer, all protected-table
   counts/fingerprints, corrected priority/outcome/evidence and proposed analysis
   ID. Missing sources, mismatched identities, unsettled/ambiguous delivery or
   incomplete analysis fail closed. No network request or DB write occurs here.
6. **Only after separate approval**, apply that exact plan against its unchanged
   staged checkpoint using the recorded checksum:

   ```sh
   python -m mozes.alert_correction --db /protected/staged/mozes-live.db \
     --expected-db-sha256 <reviewed-current-checkpoint-sha256> \
     --apply-plan /protected/atos-plan.json
   ```

   This appends one versioned `alert_analyses` record and changes only the
   existing analysis-job pointer. Original analyses, source documents, changes,
   outbox payload/status, sent receipts and Edge links remain intact. All those
   tables are fingerprinted before/after in one isolated transaction. A stale
   plan fails; repeat application is a no-op. No enqueue, delivery, processing,
   issuer sync or ACK function is called.
7. Generate a fresh durable checkpoint/manifest and successfully upload the
   new artifact using the normal lineage format **before publishing**. Include
   the plan and before/after fingerprints in protected audit evidence. A dedicated
   maintenance workflow must be separately reviewed before use; this PR does
   not introduce or dispatch one. Do not reuse stale-recovery overrides.
8. Re-export site data and the independent alert feed from the corrected
   checkpoint. Publish only through the established authorized publisher and
   Pages path, maintaining sequence ordering. Confirm feed revision changes,
   exactly one canonical ATOS entry, P2/unknown/conditional CVR and faithful
   evidence. Confirm same receipt identities/status/timestamps and same ACK.
   In an open browser confirm no correction popup or resend, including reload.
9. Verify the next automatic monitor restores the corrected newest artifact.
   Confirm all other changes/receipts/ACKs and blocked-source retry deadlines
   survive. Do not replay events or activate external delivery.

### Failure and rollback

Before durable upload, discard only the **disposable staged copy**, preserve
logs and leave the production lineage unchanged. If publication fails after
durable upload, retry only export/publication from the newest checkpoint after
reconciliation; never reset receipts or repeat notification processing.

If the display overlay itself must be withdrawn after upload, restore the newest
checkpoint under the same lock, preserve it and record its SHA256. Separately
approved rollback uses compare-and-set semantics:

```sh
python -m mozes.alert_correction --db /protected/staged/mozes-live.db \
  --expected-db-sha256 <reviewed-newest-checkpoint-sha256> \
  --rollback-analysis <correction-analysis-id>
```

Rollback selects the original job pointer/status/attempts/retry deadline while
retaining both immutable analyses and every delivery record. It refuses to
overwrite a newer unrelated analysis. Upload another fresh checkpoint before
republishing. This restores the **known incorrect** old presentation, so prefer
fixing a defective projection forward; rollback is not evidence that the old
P1/quote is trustworthy. Never restore an old whole database.

## Validation and remaining limitations

Offline tests cover the exact filing/exhibit, long negation, wrapped qualifiers,
milestone/background distinctions, genuine non-watchlist P1 approvals/CRLs/
pivotal outcomes/holds, routine filings, financing/M&A, source quote integrity,
AI contradiction prevention, correction/rollback/idempotency/stale-plan guards,
receipt preservation and browser popup/poll/reload/navigation contracts.
Existing Edge reconciliation tests continue to prove stable IDs and no duplicate
delivery for completed events. Full Python/JavaScript and CI checks are required.

Rules are conservative deterministic language classification, not a general
semantic model. Long ambiguous evidence may be omitted; source links remain.
Other historical alerts are not mass-reclassified. ATOS remains incorrect in
production until the separately approved correction is durably published.
The Worker and D1 behavior are unchanged, so this change adds no D1 polling
reads. The one-time correction fingerprints the local SQLite checkpoint only.
Stage B and migration 0005 remain unauthorized.
