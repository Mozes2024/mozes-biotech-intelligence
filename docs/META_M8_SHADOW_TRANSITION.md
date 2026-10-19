# META-M8: read-only shadow bridge, no production cutover

Post-PR58 integration baseline: main
`b96b029f12726093f377d7b6f2b0fb31733965d4`. Push CI `39313763120` and
the genuine automatic `workflow_run` Pages run `39314180874` passed. Restore,
export, preflight, deployment and ledger ACK completed; request/artifact/public
receipt, code SHA, checkpoint hashes and public data bytes were verified.
This run restored in 71 seconds without retries; it does not certify continuous
producer-contention liveness. A normal merge of this main preserves reviewed
PR57 HEAD `d06442c0177306f6690fa92873a916c13b8fea3b` and leaves only the six
shadow files in the incremental diff. PR57 remains Draft and unmerged;
its refreshed exact-head CI and separate owner merge approval are still required.

Reviewed against main `2569ad87e18d66d162ccc9e22ef970800e63d65e` after
the owner-approved PR48 squash merge at `2026-10-19T15:52:52Z`. Its tree
`769a48ea16ac4a0a30d2b5123f75599898cb29ee` exactly matches the reviewed
staging merge result. This shadow draft now targets main. A normal merge of
main preserves the reviewed shadow HEAD `09baff8a85c354b79c69a070596043cb4a9e8b7c`
and its history; the sole CI text conflict retains both inactive packaging checks.
The staging prerequisites match main byte-for-byte and are excluded from the
incremental six-file shadow diff. No force push or duplicate staging rollout is
used. PR48 is merged but dormant; PR57 remains Draft and unmerged, requiring
exact-head CI and a separate owner merge decision. Production rollout remains HOLD.

The initial historical unstacked image run hit ECR quota; PR56 resolved that
dependency without changing the pinned image. The sequential review reproduced
a shadow defect: when selection was unavailable, the comparator returned before
rechecking snapshot hashes. The new regression fails on the old code; the fix
also refuses changed snapshots on this early-return path. Neither path writes
inputs. CI now verifies the shadow module is packaged with its control OFF.

## Implemented increment

`mozes.price_shadow.compare` opens two existing **quiescent offline snapshot
copies** with SQLite `mode=ro` and `query_only=ON`. It never invokes either DB
initializer or writer. Import does nothing; calls require the separate exact
`MOZES_PRICE_SHADOW_ENABLED=1` control (or explicit `enabled=True` in offline
fixtures). No workflow, environment configuration, monitor or consumer enables
or calls it. There is no capture, staging import, attachment freeze or network.

The report compares an explicitly pinned staging run and aware cutoff for each
stock/benchmark with legacy rows in the reviewed window. It records close and
volume differences, missing/extra sessions, selected run/response/selection
hashes and exact numerical equality. Selection refuses incomplete/unmatched
stock/XBI sessions, mixed return conventions, unavailable historical vintages
and captures after cutoff. There is no fallback, date filling or splice.
Retrying against the same snapshots produces the same report with unchanged DB
bytes. It reports `cutover=HOLD` even if all numbers match: legacy rows do not
prove instrument identity, adjustment basis, vintage or availability. Caller
declarations in staging remain attestations, not independent certification.
No investment return/performance claim or strategy eligibility is calculated.

The current actionable approval/evidence gates are tracked in
`META_M8_PRODUCTION_READINESS.md`; none is a live activation instruction.

Offline use (local paths; never point at the running monitor DB):

```sh
MOZES_PRICE_SHADOW_ENABLED=1 python -m scripts.compare_price_shadow \
  --legacy /scratch/legacy-copy.db --staging /scratch/staging-copy.db \
  --request /scratch/shadow-request.json > /scratch/shadow-report.json
```

The request has `stock` and `benchmark`, each with an explicit `ticker` and
`selection`. `selection` is the existing `select_series` contract: instrument_id,
dates, provider, price_basis, volume_basis, currency, vintage, **run_id**, **as_of**.
Use the reviewed instrument mapping and XBI benchmark; ticker labels must match
the selected run. Requests are bounded to 64 KiB, sessions/legacy rows to the
existing staging row budget. No raw response, private path or legacy source
string is emitted. Preserve reports privately; do not export them to Pages.

## Both writers and downstream integration map

- `db.store_prices`: `INSERT OR REPLACE`; callers in `cli.py`, `historical.py`,
  `live_prices.py` and `pipeline_v2c.py`. Historical imports also freeze copies
  into `historical_case_prices`. Never regenerate existing attachments.
- `ingest.prices.store`: delegates to `db.store_prices`; CSV, Tiingo and Yahoo
  adapters currently normalize away distinctions. CSV prefers adj_close; Tiingo
  uses adjClose/adjVolume; Yahoo can fall back from missing adjusted close to
  quote close. A future producer must preserve original response bytes and an
  explicit field/basis declaration before either normalization path.
- `db.load_prices` consumers: `audit.py`, `engine_v2.py`, `live_intelligence.py`,
  `radar.py`; `pipeline_v2c.py` also copies the legacy table directly. Each needs
  an explicit instrument/provider/basis/vintage/session/cutoff contract before
  cutover; projecting selections into the old replacement writer is unsafe.
- Historical consumers also read `historical_price_attachments` and
  `historical_case_prices` directly. New manifests must have separate identities;
  old snapshot/source/archive/attachment records remain immutable.
- Monitor checkpoint, restore and backup currently own the legacy monitor DB.
  Separate staging storage has no approved durable owner, quota, retention or
  atomic backup/restore pairing yet. This tool is offline and does not add it.
- Scoring, exports, notifications, delivery deduplication and investment gates
  keep their current callers. No production import or schema file changes here.

## Concrete shadow campaign before a future cutover

1. Owner approves storage/serialized writer ownership and backup/restore pairing,
   instrument mapping, provider basis policy and evidence/availability contract.
   Without those decisions keep live shadow capture and production integration OFF.
2. On approved, immutable offline response fixtures, use separate run IDs for
   split, dividend and provider-correction vintages. Retain original and revised
   runs; never infer old provenance from current provider data. Freeze only new
   manifests in the staging namespace. Pin stock/XBI sessions and run IDs.
3. Save checkpoint hashes, input response/declaration hashes, source evidence,
   selected run IDs, selection hashes and comparator JSON for every case.
   Compare expected selected fields exactly, including null volumes. Classify
   every difference by field, date, adjustment convention and vintage. Count
   missing/extra sessions and each selection-refusal reason. No tolerance is
   silently applied; any future rounding tolerance needs its own reviewed policy.
4. Require zero unexplained numeric/session/provenance differences, zero source
   hash failures and zero mutation of checkpoint/attachment bytes. Verify retries
   yield the same reports and conflicting run reuse fails atomically. Historical
   as-of tests must refuse later captures and unproven availability. Passing
   numeric comparison alone never authorizes a cutover.
5. Before any separately approved live rollout, require current-main full CI,
   snapshot/restore roundtrip covering both stores, monitor continuity and
   unchanged scoring/notifications/disabled strategy gates. Define a reviewed
   observation interval and coverage universe; this PR invents neither.
6. Authorize new producer observation first, attachment consumers second, legacy
   writer retirement last. Replace BOTH writer routes only under a separate
   approval. No backfill, migration, live observation or consumer switch here.

Rollback of this increment is removal/disablement of the offline caller only;
retain input copies, staging runs and reports. No production data rollback is
needed. Future rollout rollback must preserve every observation/manifest and
checkpoint and must not replay alerts. PR35 remains HOLD; PR46 is excluded.

## Verification

Run `python -m pytest -q tests/test_price_shadow.py tests/test_price_staging.py`
and the full repository/hosted CI. Existing staging tests cover duplicate import
dates, missing sessions, split/dividend field separation, provider corrections,
vintage/cutoff refusal, idempotent retries, immutable conflicts and frozen
manifests. New tests cover both legacy writer fixtures, split/dividend/correction
differences, stock/XBI alignment, missing/extra dates, pinned historical requests,
read-only byte identity, default OFF and refusal to initialize missing files.
Windows local testing needs tzdata (already declared in Dockerfile); Linux CI
uses its system timezone database. No project dependency is changed for that.
