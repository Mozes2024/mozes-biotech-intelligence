# META-M8 implementation: isolated, inactive price provenance path

Baseline: `828245bab4981dd66e0597d2d0d653af74f0f247`.
Implements the reviewed `C2_PRICE_SOURCE_STAGING_DESIGN.md`; that document's
implementation-authorization gate is superseded by the owner's implementation
request. Production activation, migration and historical rewriting remain prohibited.

## Verified fault and current production boundary

Both `db.store_prices` and `ingest.prices.store` replace `(ticker,date)` in the
legacy `prices` table. Synthetic split (100 -> 50), dividend adjustment (100 -> 98)
and same-provider revision (100 -> 101) reproduce loss of the previous observation.
The six legacy reproduction cases intentionally continue to document that behavior:
**this PR does not change or fix the active legacy writer**. Sixteen new feature
contracts failed before implementation because staging/selection did not exist.
No assertion is made about actual production contamination.

## Implemented storage and provenance

`mozes.price_staging.connect` explicitly initializes three tables in a **separate
SQLite database**, not in `db.SCHEMA` or `db.connect`. It refuses a database containing
legacy/foreign tables before DDL. No monitor, workflow, API, live scoring, public export
or historical backfill calls it. No production migration is performed by this PR.

- `staged_price_runs`: immutable run ID and allowlisted canonical JSON containing
  provider, instrument ID (not inferred from ticker), ticker, adapter version, UTC
  capture, response/file SHA-256, explicit vintage, inclusive requested dates,
  currency, exchange, timezone, frequency, adjustment policy and field conventions.
- `staged_price_observations`: immutable `(run_id,trading_date)` with deterministic
  SHA-256 observation ID, original/adjusted price and volume cells, and optional
  source availability timestamp plus evidence reference. Original provider `close`
  is named `raw_close` structurally; **its economic adjustment basis is not inferred
  from that name**. Explicit `price_field`/`volume_field` bind the selected cells to
  the declared basis; unselected cells have unknown basis unless separately imported
  under their own declaration. Original values are retained without zero filling.
- `staged_price_attachments`: new attachment namespace; canonical manifest and hash
  freeze selected numeric rows, observation IDs, both complete run declarations,
  response hashes, selection rationale, source timestamps and matched dates.

SQL triggers refuse UPDATE, DELETE and replacement on every primary/composite identity.
They protect normal SQLite DML, not privileged schema/trigger replacement. Same-run
identical retries are idempotent, including after reopen. Same ID with changed response,
metadata, dates or values raises `Conflict`. A savepoint rolls back the entire import
batch, including an earlier successful member. Later run/vintage observations coexist.
The application owns instrument resolution and source declarations; ticker alone is
never used as the selector's security identity.

## Adapters and data boundaries

`mozes.ingest.price_provenance.prepare_import` accepts captured **bytes**, not URLs,
and performs no network call. The offline CLI `python -m scripts.stage_price_response`
accepts `--database`, `--response` and `--declaration` paths. The declaration is a JSON
object of `prepare_import` keyword arguments including `provider`, without `body`.
No directory/file path, HTTP header, arbitrary provider metadata or raw response is
persisted; only its SHA-256 and allowlisted fields are stored. Source/policy references
require HTTPS without userinfo, query or fragment. Never put credentials in references
or declarations; a content hash is not a credential vault. No API keys are accessed.

Yahoo quote and adjclose arrays are retained separately. Missing adjusted cells remain
missing. Yahoo dates use the declared session timezone; supplied currency/timezone
metadata must agree. Yahoo supplies no separate adjusted-volume series, so requesting
one is refused. A quote column may be declared split-adjusted using explicit
`price_field='raw_close'` plus an adjustment policy; its field name does not certify raw
economic prices. Tiingo close/volume and adjClose/adjVolume remain separate. CSV uses
close/adj_close and volume/adj_volume; defaults for all adjustment conventions and
currency are unknown. An adjusted field cannot be declared raw.

Taxonomy: price `raw`, `split_adjusted`, `split_dividend_adjusted`, `unknown`; volume
`raw`, `split_adjusted`, `unknown`. Known adjusted conventions require a public policy
reference supplied by the caller. This implementation does not independently certify
provider methodology. Unknown conventions can be retained but cannot be selected.
Invalid/nonfinite/nonpositive prices, invalid volume, duplicate dates and out-of-range
sessions reject the whole import. Missing numeric cells stay null; missing selected
close makes a series unavailable. Optional volume can remain unavailable.

## Deterministic selection and historical limits

`select_series` requires instrument, provider, basis for both price/volume, currency,
vintage and an explicit list of trading sessions. It selects a **single run**, newest
capture then stable run-ID tie break, or an explicitly pinned run. If the chosen run
is incomplete it refuses; it never silently falls back, splices captures, interpolates
dates, or borrows another provider's observations. Session lists must be supplied by
the caller's reviewed calendar; the library does not invent holiday/session coverage.
Missing sessions, unknown session conventions or ambiguous provenance fail closed.

`select_pair` uses one read transaction, requires identical ordered trading dates,
cutoffs, currency and price-return convention, but preserves stock/XBI provider,
run ID, capture, response hash and provider-specific vintage independently. Provider
vintage labels are not assumed comparable across providers. `freeze_attachment`
reselects atomically; it never trusts externally modified selection results or writes
`historical_price_attachments`, `historical_case_prices`, snapshots or source archives.
Pin run IDs when creating a reproducible manifest. Reusing a manifest ID with another
case/selection refuses; creating a newer observation never changes an old manifest.

An optional `as_of` must be a timezone-aware timestamp. A capture after the cutoff is
ineligible even if a historical date or claimed availability precedes it. Every selected
row also needs a source availability timestamp AND public evidence reference not later
than capture/cutoff. Capture is never substituted for availability. CSV availability
declarations are caller attestations, not independently verified historical facts;
provider historical responses without proven timestamps cannot pass this check.
This does not make historical event scoring supported or change the existing C1 API
boundary. Revised data fetched today cannot be used as a prior-vintage snapshot.

Budgets: 8 MiB response/file, 10,000 observations/run or requested window, 64 imports
per atomic batch, 10,000 runs, 1,000,000 observations, 10,000 attachments and 8 MiB per
manifest. The CLI bounds reads before parsing (16 KiB declaration). Every refusal is
explicit; no truncation/deletion/automatic retention. Logical row/byte budgets do not
constitute a filesystem quota; a separately provisioned storage quota and reviewed
retention/export policy are required before rollout. Staging is never copied into Pages.

## Activation, rollout and rollback — separate approval required

Both `MOZES_PRICE_STAGING_ENABLED` and `MOZES_PRICE_SELECTION_ENABLED` default OFF;
only exact value `1` enables their respective library entry points. Explicit
`enabled=True` is for an authorized caller/offline fixtures. Imports alone do not
initialize a database. Nothing reads these controls in the live monitor. Setting
them **does not route legacy writers or scoring** and does not resolve production M8.
No repository variable or deployment configuration is added/changed by this PR.

Required transition to eliminate active collisions, **not executed here**:

1. Separately authorize isolated durable staging storage, capture/source policy,
   instrument mapping, storage quota, ownership, backup and checkpoint coverage.
2. Add reviewed callers that capture raw response bytes and immutable declarations
   for both live writers and historical imports. Enable staging-only observation
   under existing serialized ownership; do not infer old provenance or backfill.
3. Validate complete per-run stock/XBI windows, explicit provider/field/basis policy,
   revised vintages, coverage and unavailable results in shadow comparison. Authorize
   future attachment consumers separately; existing attachments remain frozen.
4. Under separate rollout approval, replace BOTH legacy write callers with staging
   ingestion and switch consumers to explicit selections. Remove direct replacement
   access to the shared legacy table or freeze that table. Merely projecting new
   selections back through INSERT OR REPLACE would reintroduce the collision.
5. Verify ordinary monitor/checkpoint/publication behavior and immutable histories,
   notification isolation and unchanged disabled investment gates before expansion.

Rollback before rollout: disable controls/stop offline callers; retain staging files.
After a separately approved rollout: disable new callers/selection and restore the
previous code path through a reviewed rollback, preserving all staged observations,
manifests, checkpoints and frozen records. Never regenerate old attachments, rewrite
observations, replay notifications or drop new storage as a rollback mechanism.

GO for review of the inactive implementation if exact-head CI passes. HOLD for
production rollout; **META-M8 remains unresolved in production** until the separately
authorized writer/consumer transition is complete. PR35 remains HOLD; PR46 excluded.
