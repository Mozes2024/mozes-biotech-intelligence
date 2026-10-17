# A5 evidence-aware alert review

## Current main reconciliation — October 14, 2026

PR #35 merges production main `c0aa1af489efbf01ea79e194621530f3c04d90ec`
into its existing history. Four conflicts are reconciled by behavior: keep #38's
strict notifier default and validated transport, retain A5's evidence display
and end-to-end semantic cases, combine both integration test sets, and publish
one compatible `alert-evidence-a5-recovery-v1` asset version. The B1 financial
asset remains `financing-b1-v1`; B1 implementation and monitoring are unchanged
relative to production main. The open-tab checkpoint regressions and #38's
recent-visibility regressions remain intact.

The additional activation regression runs the actual A5 Python producer fixture
through the serialized Worker handler and complete index script chain. It tests
both a six-row modern feed with a smaller legacy projection and a four-row
list-only modern feed whose legacy projection is empty. A filtered response with
the same revision must use the complete static fallback; repeated Worker outage
polls preserve every historical ID and emit no popup or system notification.
Existing A5 approval/failure cases still notify once, while milestone, CVR,
mixed and immutable legacy rows remain listed without interrupts.

Exact-head full validation and CI are reported on PR #35. Passing offline tests
are GO for review only, not proof of live Worker compatibility or authorization
to activate. Merge/deployment/activation remain HOLD pending separate approval
and the outstanding Worker/DO/Builds evidence described below.

## Final cross-version compatibility gate

### Final P0 follow-up

### Isolated promotion idempotency repair

The preservation blocker is repaired only in promote_candidate, before any
event/source/state/change/outbox/watch writer. Identical constructed payload,
effective verification state, existing source link, candidate linkage and watch
state return the existing event ID without executing INSERT/UPDATE/DELETE.
Missing retrieval metadata on the same evidence retains its original retrieval
time; an explicit new retrieval observation or new verified guidance still
updates normally. Shared db.upsert_event_state and its NULL/COALESCE behavior
remain unchanged for every caller.

Eight deterministic cases advance from 12:00:00 to 12:00:02/04 UTC. Before the
repair, three regressions fail on changed state/watch timestamps (and missing
retrieval metadata). After repair, all 63 promotion tests pass. Whole logical
database dumps and SQLite total_changes/SQL traces prove a true no-write repeat;
stored outbox delivery and edge receipt/source fields remain identical. Initial
rejection retains the established audit record; repeated rejection adds nothing.
Changed verification fields and new exact-date guidance still update, preserving
prior sources and sent receipts. No frozen-clock workaround or weakened original
assertion is used. Exact-head full CI results are reported on PR #35.

PR #34 merged on October 13 as e98cb3c25b6c3a427bb06cd55b4189d72bf82762.
PR #38 merged on October 14; the current production baseline is
c0aa1af489efbf01ea79e194621530f3c04d90ec. Its normal Pages deployment
38546398398 succeeded, with complete historical list visibility and protected
legacy projection verified. This branch merges that baseline without rewriting
shared history. Cloudflare Builds evidence and live Worker compatibility remain
unverified (existing access returned HTTP 403). No permission expansion or
Worker deployment is authorized here. PR #35 remains review-only; all A5
activation gates remain in force.

Both findings are confirmed. The original 85caa58/b682b0b HotEdge used one
response cache and one pending promise across clients. Five real fetch-path
regressions failed before correction. Cache and pending owners now partition
into exactly two normalized capabilities, unknown=legacy, with independent
five-second TTLs and coalescing. POST invalidates all variants and generation
guards reject stale cache writes. Tests cover both warm-cache orders, both
overlapping orders, unknown clients, POST overlap and TTL boundaries.

The original old-script Worker outage falls back to unprotected alerts.json
and interrupts for a new list-only row. #34 now owns the compatibility-only
static/API projection and stored metadata forwarding, without semantic changes.
The Pages compatibility gate is satisfied by #38's verified normal deployment:
protected alerts.json, complete alerts-evidence-v1.json, schema-2 successful
publication receipt and checkpoint ordering. The current branch retains #38's
feed-completeness validation, complete static fallback, snapshot freshness guards
and notification deduplication. Before activation, separately verify the compatible
Worker through the Durable Object (not only its handler), both client views,
cache isolation and outage fallback. Only then separately approve #35
producer/frontend/Stage-0. Exact-head CI does not establish live Worker readiness.

An actual offline export followed by the captured old script verifies safe
fallback, urgent once-only polling/cross-tab/reload behavior and byte-identical
outbox rows. The current complete-list client remains covered by the full index
script integration. Projection does not mutate ACKs, archives or receipts.
No rollout actions were taken. Do not roll back either safety boundary while
A5 runs; regression requires stop and separate authorization.

CONFIRMED: the captured main 6f939b8 alerts_ui.js, after an initialized empty
feed, displays a popup and calls Notification for a new serialized list-only
A5 row. Revision checks and publication timestamps do not prevent it.
PR #34 now retains the full stored feed but projects interrupt-only rows for
unversioned GET requests; client=alert-evidence-v1 requests retain the full list.
PR #35 uses that capability and alerts-evidence-v1.json as its complete static
fallback. Unversioned alerts.json and /api/alerts are interrupt-only too.
No identities, source hashes, payloads, ACKs or delivery receipts are rewritten.
Old tabs lose list-only rows until refreshed, but require no refresh to remain
safe and still notify new eligible urgent events exactly once. New clients
retain every list row. Worker-first temporarily suppresses legacy browser
interrupts whose producer lacks evidence metadata; Stage-0 is separately gated.
#34 is merged and the Pages/static compatibility boundary is verified by #38.
Live Worker/DO verification of both GET views remains a deployment dependency;
only after that evidence and separate approval may #35 producer/frontend and
Stage-0 activate. Reload an existing tab only to inspect its new UI, not as the
safety mechanism. Never roll back the compatible feed contract or #38 recovery
guards while A5 runs.
Missing displayed excerpts are not missing source verification. Oversized
quotes remain omitted with a limitation; eligibility needs explicit verified
producer metadata. The frontend explicitly discloses when an eligible event's
source quotation is absent. Missing source basis remains ineligible even when
other eligibility flags are present. Exact-head results are recorded on the PRs.

This reconciliation updates PR #35 for review only. It does not merge PR #35,
deploy the Worker, activate A5, modify production data or trigger replay/monitoring.
Historical review evidence below used baseline
`6f939b8299a5d96dc4e175e5e75baf1ec5636080`; the reconciled production baseline is
`c0aa1af489efbf01ea79e194621530f3c04d90ec`.

## Confirmed causes and bounded corrections

- Python accepted approval vocabulary, review acceptance and historical outcome phrases without a reliable completed-event distinction. Its 25-character negation guard could lose a long initial `No`.
- Stage-0 had still simpler polarity rules. Python and Worker now consume the same versioned rule data, with sentence/clause scope, completed verbs, explicit absence, prospective/conditional language, background sections, primary/secondary results, conflicting programs and review status.
- Source-publication time anchors dated references (ISO/RFC timestamps normalized to UTC). A June announcement remains a June development when analyzed later; an older dated fact in a newer filing remains background. Publication, acceptance and detection timestamps are read, never rewritten.
- Verified identity was passed as `watched or verified`. Urgency now separates watch preference, audited issuer identity, completed material evidence and interrupt eligibility. A verified non-watch issuer can receive P1; watch membership alone cannot.
- First-stage evidence reads are bounded to 4,000 headline/structured characters or 12,000 source/summary characters. A cut final sentence is excluded. No model call participates in this path.
- Regulatory submission, acceptance, priority review, Fast Track, orphan designation, IDE/IND authorization and advisory recommendations have separate outcomes. They do not become marketing approvals or urgent clinical/FDA popups.
- Relevant upcoming catalysts retain the existing list routing. Corporate CVR, incomplete evidence, historical records and ambiguous results do not create interruptive browser notifications.
- Completed Worker candidates with unchanged source/identity retain their existing classification, history and completion receipt across semantic-only upgrades. ACKed events retain their existing records. There is no policy-version bump, historical scan or migration.
- Story consolidation compares legacy and current evidence under the same semantics without altering the earlier payload. An additional source for the same announcement cannot create a second delivery merely because the policy version changed.

## Evidence contract and PR #17 mining

The 56-case authored adversarial contract was created before parser implementation. Comparing current-main and A5 on this contract changes 17 polarities and 17 materiality labels. All required cases assert polarity, family, outcome, materiality, evidence basis, confirmed outcome and review/uncertainty status. Python and the active Worker wrapper exercise the same cases with a fixed temporal reference.

Additional tests cover truncation, HTML boilerplate, ISO/RFC/timezone boundaries, real ATOS captures, source offsets/hashes, watch versus identity, non-watch urgency and fail-closed AI. Six intentional adjudications of legacy headline expectations are recorded separately in `tests/fixtures/a5/legacy_label_adjudications.json`; the original 85-row corpus, sign-flip assertion and 95% agreement threshold remain intact.

PR #17 at `67e0ff05d9ca028ca92c3b38de80d868efba557b` supplied the ATOS raw fixtures/provenance and the useful ideas of long-scope negation, conditional-CVR suppression, metadata-aware popup gating and exact evidence tests. Its old branch was not rebased, cherry-picked or merged. Its runtime implementation was adapted to current main and an explicit end-to-end contract. Direct consumer assumptions about stripped fields and broad legacy urgency were rejected. `mozes/alert_correction.py` and historical ATOS display correction were neither incorporated nor executed; #17 remains a held reference.

Quotes are complete, exact substrings of archived normalized source text, with source URL, normalized-text SHA256 and start/end offsets. Up to three separate sentences retain adverse findings and conditional antecedents; they are never spliced. A sentence above 600 characters is omitted with a limitation. Optional AI wording falls back without invoking a provider because numeric/citation checks do not prove preserved negation. Existing raw archives remain byte-for-byte intact; ATOS raw fixture hashes are pinned:

- Exhibit: `ccaa408dd2adc1964a24a5f4124075e52c5b6989eb3bc5d6d97e2e89f30e6bae`.
- Filing: `ad245669d2c68d126ef5b3ae2f08bb6f14e17e85bb6953f0efbb3ce629e9cdfb`.

## Frozen read-only assessment

Authoritative source checkpoint: producer run `38284072542`, artifact `11763050770`, timestamp `2026-10-12T17:26:54Z`, schema-2 publication run `38284294907`. Database SHA256 before and after:

`3fe3ee7d9ed00cd3fdf22978f64fd72f596e1822d620412b1fca96f29ff1333a`.

SQLite was opened with `mode=ro` and `PRAGMA query_only=ON`. Integrity passed; foreign-key violations: zero. All 34 normalized source-document hashes were verified, with retrieval timestamps at or before the checkpoint. Archived-document probes are separate retrospective evidence checks; later body reads are not projected into earlier historical alerts.

The comparison covers 1,232 immutable change records, rather than the separate 198-event scoring population. It simulates future enqueue/classification behavior from already stored event inputs. It does not replace stored payloads, histories, receipts or classifications.

| Measure | Before | A5 counterfactual |
| --- | ---: | ---: |
| Positive polarity | 70 | 17 |
| Negative polarity | 23 | 3 |
| Mixed polarity | 1 | 0 |
| Unknown polarity | 1,138 | 1,212 |
| P1 | 11 | 3 |
| P2 | 759 | 770 |
| P3 | 462 | 459 |

There are 74 polarity changes and 15 priority changes (8 P1→P2, 5 P3→P2, 2 P2→P3). Thirty-eight old outcome classifications become explicitly historical context. Three ACHV review-acceptance rows from one historical filing previously received positive polarity; they are historical milestones, not new FDA approvals. The old classifier did not expose an FDA outcome label, so this is a vocabulary/polarity conflation count, not evidence of three actual approvals.

Across old alertable P1/P2 candidates, 22 lose interrupt eligibility; three source-backed records remain eligible (one Nasdaq halt and two BMY source records for the same clinical announcement). In the 44 distinct stored Stage-1 alerts, the counterfactual policy retains two eligible records and suppresses 42 interrupts. These two counts use different populations and must not be conflated.

Existing stored legacy payloads retain their original polarity/priority and immutable IDs. Their absent popup metadata fails closed; all 44 historical payloads lack the new complete contract and are list-only under the new browser policy. This is not a reclassification or a notification replay. Existing ATOS's historical positive display remains visible and explicitly marked as a preserved historical classification.

Byte-identical database verification preserves all tables, including 1,232 changes, 112 source archives, 44 outbox rows, 80 analysis rows, 34 explanation-source documents and zero forward candidates. No scoring, financial/PIT availability, historical score versions, publication receipts or OOS gates changed.

Reproduce with:

```text
python -m scripts.assess_a5_checkpoint <verified-read-only-checkpoint> <output.json>
```

The JSON contains complete before/after counts by change type, polarity and priority, every changed record with identity/URL/source hash, and the separate archive-text classifications. It must not be used as a production repair tool.

## Eight representative source-backed examples

1. **ATOS, CHG-01557485de654f94415ef482** — [SEC exhibit](https://www.sec.gov/Archives/edgar/data/1488039/000119312526418560/atos-ex99_1.htm): legacy positive/P1 becomes unknown corporate `conditional_cvr`/P2 in the future-policy simulation. The denial of FDA approval and conditional future payment remain intact in complete quotes. The real historical payload is not corrected.
2. **ACHV, CHG-a09991280fbec0714ad09531** — [SEC filing](https://www.sec.gov/Archives/edgar/data/949858/000119312526344624/achv-20260630.htm): September 2025 NDA review acceptance is a historical milestone, not an approval or a new event. The two other rows from this same filing explain the three-row conflation count.
3. **ALNY, CHG-66aa9891e0f475f19a965e67** — [SEC filing](https://www.sec.gov/Archives/edgar/data/1178670/000117867026000062/alny-20260630.htm): a September 2023 KARDIA-1 result mentioned in a later financial filing is historical context; it cannot generate a new clinical-result popup.
4. **GMAB, CHG-6d3bd05a0c72de605825059d** — [Genmab announcement](https://ir.genmab.com/news-releases/news-release-details/genmab-and-abbvie-announce-epcoritamab-combination-r-chop): statistical improvement in progression-free survival retains positive textual polarity, but the stored first-stage text does not explicitly establish the primary endpoint. It remains P2/list-visible, with review required, rather than assuming a verified primary outcome.
5. **BMY, CHG-96517ee741a30f943b7edd96** — [ALOFT-IPF announcement](https://www.businesswire.com/news/home/20261011397472/en/Bristol-Myers-Squibb-Announces-Phase-3-ALOFT-IPF-Trial-Evaluating-Admilparant-in-Adults-with-Idiopathic-Pulmonary-Fibrosis-Met-Primary-Endpoint): an explicit primary endpoint success plus audited issuer identity retains positive/P1 and source-backed interrupt eligibility. Its second source record does not create an additional stored alert.
6. **AVBP, CHG-7ed914104eb182c12d4ecf91** — [Nasdaq halt source](https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts): structured exchange evidence retains urgent eligibility independently of clinical polarity. Existing T3 wording still warns that the code does not prove the stock is currently halted.
7. **VIR, CHG-31cb76932493227a116f38f2** — [Fast Track announcement](https://www.businesswire.com/news/home/20261008428695/en/Vir-Biotechnology-Announces-PSMA-targeted-PRO-XTEN%C2%AE-Dual-masked-T-cell-Engager-VIR-5500-Received-FDA-Fast-Track-Designation-for-the-Treatment-of-Prostate-Cancer): a real designation becomes a distinct unknown-polarity milestone, remains available for research, and cannot become an approval/P1 popup.
8. **Front Range, unattributed source** — [FDA trial-clearance announcement](https://www.businesswire.com/news/home/20261006387219/en/Front-Range-Pharmaceuticals-Announces-FDA-Clearance-to-Initiate-Phase-2-Trial-of-FRP-003-in-Long-COVID): IDE/IND trial authorization is a milestone, not a marketing approval. Missing verified issuer identity keeps P3 and prevents interruption; no ticker or financial evidence is invented.

## Integration validation and deployment dependency

The actual Python outbox and small feed are serialized through Worker `publicFeed()`, then loaded by all browser scripts in `index.html` order. The regression verifies list visibility, complete evidence/provenance, eligible positive/negative notifications, suppressed milestone/CVR/mixed/legacy notifications, repeated-poll dedup and unchanged WATCH/research dashboard composition.

A real local browser loaded `alerts_ui.js?v=alert-evidence-a5-v1` alongside the unchanged #33 app/Live Intelligence versions and #31/#32 checkpoint-health version. Natural alert polling showed exactly two popups from six producer/Worker-serialized fixture rows; all six remained listed. A normal reload produced no repeated popup. One research section with six cards and six WATCH cards remained composed. This is local integration verification with explicit test alerts and a frozen dashboard fixture, not production verification or native OS notification delivery; system-notification calls are covered by offline tests.

The feed-contract dependency from PR #34 is merged into main at `e98cb3c25b6c3a427bb06cd55b4189d72bf82762`. Historical dependency CI `38288481139` remains provenance, not current activation evidence. The current production Pages boundary includes #38 at `c0aa1af489efbf01ea79e194621530f3c04d90ec`, verified by successful deployment `38546398398`. The compatible Worker contract preserves semantic metadata and bounded exact citations, and accepts `source-rules-v2`; its actual live deployment still needs separate verification.

Before A5 producer/frontend activation, a separately authorized deployment must install and verify the #34 Worker contract. An older Worker strips policy fields and rejects the new explanation method; successful Python/frontend tests cannot prove that deployment dependency is satisfied. The A5 Stage-0 code also requires explicit Worker deployment approval. No deploy command or activation hook is included here.

Current validation includes the complete Python suite, shared adversarial Worker tests, public-feed tests, all 95 checkpoint/timezone/dashboard tests, existing Worker/SQLite/ACK/cache/fairness suites, local D1 query plans/24-hour workloads, compile/syntax checks and offline bootstrap/export smoke. Exact final-head CI, including Docker Python 3.11.17 packaging, is reported on the A5 PR rather than claimed from the earlier dependency run.

Residual limitations: deterministic English patterns are conservative outside the supported grammar; marketing/secondary-only results need review; late source enrichment never silently rewrites an already-sent payload or creates a second notification for its old ID. Missing evidence is neither adverse evidence nor an investment recommendation. Merge/activation remains HOLD until independent review and a separately approved dependency/deployment plan; no next milestone is started.
