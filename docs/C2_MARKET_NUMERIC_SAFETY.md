# C2 / META-m01: malformed price safety

Production baseline: `c0aa1af489efbf01ea79e194621530f3c04d90ec`.
The active engine divides closes and takes logarithms without validating inputs.
Both price writers accept zero/negative/infinite values; ingest.prices.store also
accepts malformed numeric text. SQLite maps NaN to NULL: close then raises a storage
constraint error, while a NaN volume can silently become missing. This is not proof
that production contains any particular malformed row.

## Minimal correction

`price_validation.validated_prices` accepts canonical daily dates, finite positive
closes, and optional finite nonnegative volume. Zero volume is valid; absent volume
stays None. Booleans, malformed strings, NaN/infinity and invalid dates are rejected.
No missing value is replaced with zero, and no numeric magnitude is arbitrarily
clamped. The entire batch is validated before any SQL write. Both writers share
this boundary; the existing database schema is untouched.

Yahoo/Tiingo/CSV results use the same validation. Yahoo no longer treats an explicitly
zero adjusted close as absence and silently falls back to raw. Missing adjusted
data can still fall back to raw under the existing adapter contract; valid raw/
adjusted provider ambiguity is explicitly deferred to the separate staging design.

The active market reader validates old stored rows without updating them. An invalid
stock series yields available=false / invalid_stock_prices and no return/volatility
fields. Invalid benchmark data produces no relative returns and explicitly reports
benchmark_data_status=INVALID, while valid standalone stock returns remain available.
Missing benchmark days remain an actual-date inner join, with benchmark_as_of showing
the paired series endpoint. No forward fill, row-index alignment or invented zero
relative return is introduced. Existing classification/gate requirements are unchanged.

The original return/volatility formulas are unchanged for valid inputs. Arithmetic
overflow, underflow-to-zero logarithms and nonfinite calculated outputs return
available=false / invalid_market_arithmetic. They do not crash or emit NaN/Infinity.

## Regression evidence

Before correction, the new core suite had 35 failures / 1 pass. Tests reproduce
unsafe replacement under both writers, existing zero/negative/nonfinite/text data
reaching active calculations, invalid volume, zero-adjusted Yahoo fallback and
unsupported extreme finite arithmetic. Already-clean malformed-close rejection
in db.store_prices was distinguished from missing guards.

After correction, focused suites cover positive/finite checks, atomic rejection,
valid missing/zero volumes, unchanged formulas, missing benchmark days, provider
adapter fixtures, raw/adjusted CSV split values, preserved case-frozen rows and full
active event scoring returning explicit unavailable market data without price rewrites.
All provider requests are offline doubles; no credentials or live market feed are used.

## Remaining boundaries

Valid cross-provider/basis/vintage replacement in the shared staging table is not
fixed here. The independent META-M8 design requires separate authorization before
schema/staging/selection changes. Existing frozen attachments are not reprocessed,
relabeled or repaired. Values already stored as NULL cannot reveal whether their
original volume was missing or NaN; provenance is needed to distinguish that history.
Guarding invalid market inputs does not change investment gates or guarantee that
all other evidence-based classifications require a market component.

No database migration, production rewrite, historical replay, scoring weights,
notification eligibility, B1/B2/A5/PR38 changes, merges or deployments are included.
GO for review of numeric safety; HOLD for provider reconciliation and production action.
