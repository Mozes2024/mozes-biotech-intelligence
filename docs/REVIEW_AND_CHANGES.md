# Independent Prototype Review and v0.2 Synthesis

Two independent prototype directions were reviewed before v0.2.

## Retained from the first prototype

- strong point-in-time leakage guards;
- immutable paper ledger;
- explicit score contributions;
- model versioning;
- conservative default behavior;
- static Hebrew RTL research UI.

## Problems fixed from the first prototype

- live scoring previously read static JSON while ingestion wrote elsewhere;
- nightly workflow did not perform actual discovery;
- stale/resolved events could remain upcoming;
- secondary-only source dates could sit in the live list;
- clinical and regulatory events shared too much scoring logic;
- HOLD could theoretically unlock before sufficient empirical validation;
- price/benchmark logic was not database-driven.

## Retained from the second prototype's design ideas

- treat biotech as a mispriced-binary research problem, not a calendar problem;
- separate PDUFA from first pivotal readouts;
- preserve every run-up grid instead of selecting a pretty window;
- handle after-hours/pre-market timing;
- do not require a pre-catalyst run-up as a positive filter;
- keep HOLD-through rare;
- freeze paper signals before outcomes.

## v0.2 synthesis

v0.2 makes source verification and event lifecycle first-class, keeps CT.gov in discovery-only status, uses SEC/EX-99 for promotion, separates evidence engines, aligns market data by trading date, and refuses to unlock trading-oriented labels until an out-of-sample validation gate is satisfied.
