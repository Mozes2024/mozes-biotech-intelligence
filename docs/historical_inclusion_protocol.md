# Historical catalyst inclusion protocol, frame v1

This is a **source-first convenience frame**, not a representative or randomized sample of all biotech catalysts. It is frozen in `mozes/data/historical_candidate_frame_2024_2026.json` before outcome labels or price returns are consulted. The fixed frame contains 25 issuer-announced 2024–2026 Phase 2/3 topline or FDA decision candidates. The three existing clean cases are retained separately. No candidate may be removed because its clinical outcome or stock move is unfavorable.

## Candidate construction

1. Search issuer IR pages and issuer-distributed GlobeNewswire/PRNewswire releases for prospective phrases (`to announce topline`, `to report topline`, `FDA accepted`, `PDUFA target/action date`) with 2024–2026 event dates. Exclude Phase 1, interim-only, secondary analyses, non-US listings, and events already in the clean starter bundle. This first frame was assembled on 2026-10-01; its URLs, tickers, type and prospective date are frozen in the JSON file. Discovery through a search engine is not proof of completeness.
2. Treat `(issuer ticker at event time, program/application, announced decision or readout)` as the event key. A second trial or FDA application for the same ticker is a distinct case. Sort by planned event date, catalyst type and candidate ID for processing; process every entry in the frozen frame, including failures.
3. The candidate frame contains **no outcome, price, return or success field**. Selection and exclusion are based solely on source availability, event identity, price coverage and the timestamp rules below. Maintain an exclusion ledger with an enumerated missing-evidence reason; never silently replace an excluded case with a known winner.

## Case acceptance and freezing

- Require a primary issuer/SEC/FDA source published before the feature snapshot. Record canonical URL, source type, original publication time and time-zone basis, retrieval time, SHA-256 of the archived fact extract or page, and the exact facts used. If only a calendar date is proved, use a conservative end-of-day publication timestamp; do not pretend it was known earlier.
- Freeze the feature snapshot at a documented T-60/T-30/T-14/T-7/T-3/T-1 horizon when that horizon is actually supported. Copy only facts explicitly present in pre-event sources; a planned PDUFA date is not the decision announcement date. Never derive a clinical readout date from ClinicalTrials.gov primary completion.
- After freeze, require a **separate** issuer/SEC/FDA outcome source. Record the actual announcement time and market session if published or corroborated by an issuer-distributed timestamp. If the exact release time cannot be established, retain the candidate in the exclusion ledger instead of inventing a time or session. Label success/fail/mixed or approve/CRL/delay using source wording, not stock price.
- Require event-window ticker and XBI adjusted daily closes from a documented keyless/CSV adapter. Attach and freeze price rows under `case_id`, with provider URL, capture timestamp and CSV hashes. Missing or ambiguous historical ticker identity, price coverage, source provenance or session excludes the case from empirical readiness.
- The immutable source archive, blinded snapshot and post-event label are separate records. Legacy `post_hoc` cases are never promoted by this frame and never enter empirical backtests. New cases do not alter the independent OOS/walk-forward validation gates.

## Reporting and continuation

The selection ledger reports every candidate as included or excluded with a missing-source/price reason. Dataset status reports cases by catalyst type, year, clinical/regulatory outcome and readiness, distinguishing the 21 legacy quarantined cases. A future batch must freeze a new source-first candidate frame and its search date before labels/prices are inspected. Re-running the same frame and bundle must be idempotent; edits to archived source content under an existing source ID are rejected.
