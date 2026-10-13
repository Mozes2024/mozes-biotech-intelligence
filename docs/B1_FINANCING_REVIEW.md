# B1 financing and realized proceeds review

Base: `e98cb3c25b6c3a427bb06cd55b4189d72bf82762`. B1 does not include held PR #35.

## Confirmed findings and scope

Available Five-LLM adjudication: `CLAUDE_PASS2_DELTA_AND_ROADMAP.md`, META-C5
(closing wording), META-M9 (global foreign-currency exclusion), META-M10
(amount anchor after amount), META-M11 (realized ATM sales classified as capacity).
The complete original integrated audit was unavailable locally; no additional
finding identifiers are inferred. The available master roadmap assigns financing
status and capacity-versus-cash distinction to B1.

The initial 27 adversarial contracts were added before implementation: pinned
main failed 18 and passed 9. Coverage includes positive, negative, conditional,
cancelled, proposed, priced, closed, shelf, ATM capacity/sales, mixed transactions,
convertibles, warrants, secondary resales and explicit foreign currencies.

## Change

Extend the existing classifier, retaining its interface and stored JSON schema.
Local transaction statuses and exact source spans distinguish realized gross/net
receipts from authorization, convertible principal, use of proceeds and conditional
amounts. Explicit currencies are retained without conversion. Reporting period
and economic date inform reconciliation against the statement period. Uncertain
amount/status/recipient or overlapping transactions require review. Gross receipt
verification does not establish net cash; no amounts are added to balances or runway.

Context reads respect publication and capture availability at the UTC date cutoff.
Identical dated source text across exhibits/amendments is grouped with all source
provenance retained. Different source text with overlapping amounts is marked for
review rather than totaled or silently declared separate cash receipts. Distinct
transactions remain visible. Legacy immutable payloads are not reclassified or rewritten.

Financial details show currency, period, gross/net/capacity/principal labels and
explicit limitations. Only the financial-context script asset version changes.
Research selection, WATCH, alert identities, receipts, gates and Worker code are
unchanged. No API, migration, historical backfill or production action is included.

## Frozen assessment and limits

Read-only checkpoint: source run `38385514066`, artifact `11805252388`,
checkpoint `2026-10-13T10:48:08+00:00`.
SHA256 `e11100eb24dc9ecaab91a81e332ccc3273f194818a0d57a30559daee47a8a64b`.
SQLite integrity OK, zero foreign-key violations, database bytes unchanged.

29 immutable financing disclosures; 172 distinct stored fact contexts within
their records. Full original filing texts are absent from the checkpoint archive.
Local SEC collection is disabled without configured SEC_USER_AGENT. Therefore
there are **zero hash-verified full-document counterfactuals and 29 unresolved
whole-document classifications**, not a claimed full-filing improvement count.

The executable excerpt assessment processes each stored context independently;
it never joins excerpts into invented source text. Results and grounded SEC URLs
are in `tests/fixtures/b1/checkpoint_assessment.json`. It records changed excerpt
results separately from filing classifications: 45 changed excerpt results across
16 disclosures, including 38 changed excerpts requiring review. The current legacy context exposes
8 MRNA facts instead of 6 by preferring detailed primary sources to empty exhibits.
No historical cash receipt is newly declared verified from these excerpts.

Grounded examples:
- MRNA: closing language recognized; $3B convertible principal is not net proceeds;
  $285M capped-call cost is use of proceeds, not a financing receipt.
- SMMT: signed $2B preferred-placement agreement remains proposed/unverified cash.
- AMLX: estimated $471.7M/$542.5M option-dependent net proceeds remain conditional.
- BIVI: $6,464,341 offering capacity remains capacity, not realized cash.
- CRDL: USD573,750 potential warrant proceeds are not realized cash.
- ABVX: USD and EUR facts are retained locally without conversion or summation.

Reproduce from repository root:
`python -m scripts.assess_b1_checkpoint <frozen-mozes-live.db>`.
Optional `--documents <directory>` accepts `<source_hash>.txt` only after exact
digest verification; mismatches abort. No network or database writes occur.

## Review gates

Focused review correction: expanding coverage to 10-Q/10-K/20-F must not emit
new changes for previously published reports of already-baselined issuers. An
issuer-specific periodic coverage start timestamp is persisted in the existing
v2c_state before source I/O, including empty, failed or partial initial scans.
Reports published at/before that boundary enrich context quietly; reports
published after it retain normal one-time change detection. Publication date,
not the reported ATM period, determines whether information is new. Same-day
date-only sources are conservatively treated as initial coverage. Existing
8-K/6-K behavior and immutable rows are unchanged. Deterministic regressions
reproduced 5 failures / 2 passes before correction, including a real CHG from
a historical 10-Q; after correction history creates no CHG or outbox candidate.

Full Python, production JavaScript chain, Worker/D1 regressions, smoke, syntax
and Docker checks must pass against the final PR HEAD. No merge or deployment
is authorized. Full historical impact remains limited by archived source coverage;
this limitation must not be represented as verified full-filing classifications.
