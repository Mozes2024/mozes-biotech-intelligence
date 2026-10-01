# v2C: financial context, identity-safe guidance, and budgeted monitoring

Target baseline: `8ba9d8a8ac51bba34727be988bc8e227471da62c`.
This is an additive, manually uploaded extension. It does not change clinical evidence
weights, recommendation thresholds, historical case data, or empirical validation gates.

## User-visible result

Existing Hebrew cards gain a cash-runway estimate, the latest financing-disclosure type,
and a warning when the constant-burn estimate falls before/inside the catalyst window.
The detail drawer exposes the reporting period, filing date, inputs, source, and literal
financing fact excerpts. Missing values stay missing. A later completed financing is
explicitly flagged for reconciliation; it is not silently added to old reported cash.
The existing KOD past-event/future-catalyst distinction is preserved.

## Cash/runway: supported calculation and boundaries

`financial_intelligence.py` reads standard SEC companyfacts US-GAAP/USD facts. It checks
CIK, cutoff, accession, units, duration and finite numeric values. Same-day filing dates
are excluded because companyfacts does not establish intraday public availability.

Liquidity uses cash/equivalents plus at most one recognized current-investments tag,
on the same period and accession. It never sums overlapping alternative tags, treats
restricted cash as unrestricted, or assumes a missing investment balance is zero.
If investments are missing, the estimate is explicitly cash-only/lower-bound.

Operating burn uses a reported quarter where supported. Otherwise it subtracts matching
YTD observations only on the same filing basis; when that is unavailable it uses the
reported-period average and says so. Monthly conversion uses 365.25/12 days. Remaining
runway subtracts elapsed time since the statement under a constant-burn assumption.
Statements older than 180 days are not current runway estimates. Nonnegative operating
cash flow does not mean infinite funding. Capex, future financing and new commitments
are not modeled. The model does not calculate financing probabilities or market cap.

IFRS, issuer-specific/custom tags, unsupported currencies, ambiguous restatements and
missing period-matched facts are not guessed. Some issuers will therefore show missing
information even when a human can find the number in a report. This is deliberate.

## Financing interpretation

Forms: S-1/S-3 (including amendments/ASR), 424B3/4/5, 8-K and 6-K.
The deterministic classifier separates registration-only, ATM capacity, proposed,
priced, completed, resale and review-required/multiple-transaction disclosures.
Form type alone does not prove a cash raise. Dollar amounts are tied to local textual
anchors, and each extracted fact carries its literal span/context and source-text hash.
Ambiguous multiple amounts are retained as ambiguous. Warrant exercise prices are not
used as offering prices. No inferred dilution percentage or cash-balance adjustment is
made. This is bounded rule-based interpretation, not full legal-document understanding.
A first successful pass per issuer establishes a quiet baseline. Later new disclosures
create deduplicated `financing_interpreted` changes. A missing issuer does not suppress
alerts for other issuers. New source-backed cash facts can create `cash_runway_updated`.

## Identity and regulatory lifecycle

The full current-security candidate relation is keyed by CIK and ticker. Automatic
sponsor mapping uses only unambiguous, explicitly described US common/ordinary equity
or depositary shares on supported exchanges. Warrants, units, rights, preferred shares,
ETFs and test issues are excluded. A final W/U/R letter alone does not disqualify a real
stock. Multiple eligible classes require an unambiguous existing verified choice;
normalized-name collisions or conflicting CIKs remain unresolved. Ineligible current
symbols do not retain stale CIKs for financial extraction. Historical identities are
not remapped or overwritten.

Regulatory guidance updates one identified application, not a hash of ticker and date.
Old guidance is retained in chronology and immutable revision records. A PDUFA extension
can supersede the prior date within that same event. Advisory-committee dates do not
overwrite PDUFA action dates. Older/broader statements cannot erase newer precise
guidance. Conflicting date-only disclosures on the same day need review. Terminal
applications are not reopened. SEC acceptance time is never assigned as outcome time.

The automatic identity vocabulary intentionally covers only reviewed existing
applications. New/ambiguous applications go to `v2c_regulatory_review`; this is not a
universal application-name extractor. Legacy AUTO-REG date-keyed rows with no application
identity are retained but quarantined. ClinicalTrials.gov remains discovery/investigation,
not verification of a readout date. The older clinical-readout promotion path is not
rewritten by this extension.

## Storage and provenance

New tables are additive and prefixed `v2c_`; existing schema.sql is not replaced.
Financial snapshots, interpreted financing records and regulatory revisions have
update/delete blocking triggers. All input facts carry source metadata/hashes. Full raw
companyfacts/documents may exist in the bounded HTTP cache, but that cache is NOT a
permanent archival guarantee. Source archives, historical case-bound prices and OOS
validation are unchanged. Audit extracts and SQLite triggers are not independent proof
of immutable history against a database administrator.

The cumulative monitor database is restored between runs. Artifact copies still expire
after 90 days; if no surviving state artifact exists the system bootstraps and reports
missing live evidence. This release does not provide external permanent storage.

## Workflow Diet

- CI: one Python 3.11 runner, full repository pytest, Python/JS syntax, offline payload
  smoke. Code/workflow path filters exclude documentation-only changes. Cross-version
  3.10/3.12 CI matrix coverage is no longer automatic; this is an explicit cost tradeoff.
- Pages: after successful main-push CI, or an explicit trusted producer dispatch. It
  restores stored data and exports locally. No SEC/CT.gov scan, duplicate pytest, or
  price refresh in a code-only deployment. The checked-in web/data.json is not authoritative.
- One monitor producer: four passes/day at 00:20, 06:20, 12:20 and 18:20 UTC, weekends
  included. Monday/Wednesday/Friday 06:20 UTC also requests deep discovery. The exact cron
  event determines deep mode, so scheduler delay does not select the wrong mode.
- Deep discovery has a 480-second subprocess budget; incomplete work is recorded as
  partial, never as a successful complete scan. Financial and financing loops have
  120-second work budgets. The whole producer has a 20-minute workflow limit.
- Producer stores data once and dispatches Pages when a semantic change or daily
  heartbeat warrants it. A same-day price change and a new CT.gov/change-log cursor count
  as changes, not only a new price date. No-change passes do not dispatch Pages.
- Old nightly workflow has no schedule and is retained only as a deprecated notice.
- SEC document cache is integrity-checked and bounded to 8 MiB. Requests are limited to
  4/sec/process, with bounded retries on transient errors. 403 is not repeatedly retried.

No cloud/API subscription, new API key, LLM provider or deployment service is required.
The existing `SEC_USER_AGENT` remains necessary for SEC collection. Yahoo live prices
remain the existing best-effort adapter, not a guaranteed licensed real-time feed.

## Important operational qualifications

GitHub cron is best-effort, not a precise alert-time SLA. Publication can be delayed by
queues/failures. The producer records its publication request, not a third-party delivery
receipt; after a failed dispatch/deploy, the next material change or daily heartbeat can
retry. HTTP cache/state downloads also consume storage/network resources. Actual runtime
savings must be measured after deployment; no precise percentage is claimed here.

The new financial layer populates on the next scheduled producer pass after upload.
The immediate code deployment is intentionally offline and may show "missing information"
until then. Do not trigger an extra deep scan just to make the new fields appear.

## Verification boundary

See the package VALIDATION_REPORT.md. Focused tests and source syntax were run locally.
A complete, current repository checkout was unavailable in the authoring environment,
so full current-main CI, live SEC ingestion, and production browser verification are NOT
claimed. After the final upload commit, the normal CI provides that repository-level check.
