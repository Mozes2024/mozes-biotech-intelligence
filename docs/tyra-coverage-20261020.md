# TYRA identity and historical staging

SEC 8-Ks 0001193125-26-385886 and 0001193125-26-390951 identify Tyra
Biosciences, Inc., ticker TYRA, CIK 1863127. The retained production checkpoint
already contains that SEC-v2C-equity mapping and NCT05544552. It contains no TYRA
change row. This establishes mapping/candidate presence and ledger absence;
private per-event Edge evidence is still required to identify the first divergence.

The verified official site is https://ir.tyra.bio/. No RSS endpoint is assumed.
Only the company name is added to alias resolution: bare dabogratinib or SURF302
does not identify an issuer. The reviewed results release also corroborates
dabogratinib, SURF302, SURF303 and BEACH301; these are context, not standalone
issuer credentials. All forty priority issuers and the request budgets remain.

`mozes.tyra_history.import_history` stages two reviewed historical ledger records
in an explicitly supplied candidate connection. It has no live caller, fetch,
sender, ACK or publisher. Its accession identity binds each reviewed SEC/IR pair.
The permanent `historical_only` metadata is enforced by the common enqueue
function, including direct Stage-2 enqueue and later synchronization attempts.
History remains available through `recent_changes` and the modern change list.
No delivery receipt or outbox row is manufactured. Production import is pending
the separate recovery gate; merging this code does not import data.

- September 9: initial Phase 2 SURF302 results, not an upcoming conference.
  https://ir.tyra.bio/news-releases/news-release-details/tyra-biosciences-reports-initial-phase-2-surf302-results
  https://www.sec.gov/Archives/edgar/data/1863127/000119312526385886/d167841d8k.htm
- September 14: offering pricing, approximately $400 million expected gross,
  before expenses. Expected September 15 closing is conditional. This record
  establishes neither closing, net cash received nor ATM availability.
  https://ir.tyra.bio/news-releases/news-release-details/tyra-announces-pricing-400-million-underwritten-offering-common
  https://www.sec.gov/Archives/edgar/data/1863127/000119312526390951/d102025d8k.htm

Dates are day precision; midnight UTC is a serialization boundary, not an asserted
release time. Subsequent closing evidence must be reconciled independently.
Offline regression fixtures also exercise TYRA and an unrelated non-priority
Phase-2 issuer through SEC ingestion, classification, change ledger and Stage-1
list, with both positive and negative outcomes and accession replay suppression.
