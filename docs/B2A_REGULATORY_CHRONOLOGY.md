# B2a / META-M13: unknown publication dates

Confirmed against production main `c0aa1af489efbf01ea79e194621530f3c04d90ec`.
The availability helper `pub_effective(None)` deliberately returns a distant
sentinel for point-in-time exclusion. Regulatory recency incorrectly used that
sentinel in `max(active_dates)`, rejecting every real filing as `older_source`.
An absent `date` key also raised `KeyError` while building date information.

Correction: only chronology entries with a publication date contribute to the
regulatory latest-publication bound. Missing keys are treated like unknown dates
when normalizing the timeline. The original stored chronology is retained, and
the global availability helper/PIT cutoff is unchanged. Known month/year dates
still use conservative period ends; regulatory versions still bound recency.
Source identity, future-publication exclusion, terminal-state protection and
same-day conflict review are unchanged.

Before correction, `python -m pytest -q tests/test_regulatory_v2c.py` produced
4 failures and 14 passes: null/empty publications rejected a valid filing,
an absent key crashed, and an undated entry hid a real same-day conflict.
After correction, the regulatory and date suites passed all 35 tests. The new
tests cover original history/provenance, exact publication timestamps, retrieval
timestamps, retry without writes, out-of-order filings, conservative partial-date
bounds, missing/future source publications and issuer mismatch.

No event IDs, source records, histories, ACKs or receipts are migrated/replayed.
No production action is included. Date-only conflicting disclosures still require
human reconciliation; this change does not infer intraday order or outcome time.
