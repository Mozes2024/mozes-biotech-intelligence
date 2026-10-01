# MOZES Live Intelligence v2B — manual GitHub upload

Base expected: current `main` around `b4fc0bdef98f2245f99f33748fabbe13fde85ca2`.

Upload/replace these files at the exact repository paths:

- `mozes/live_prices.py` — **new**
- `mozes/live_intelligence.py` — replace
- `mozes/payload_v3.py` — replace
- `web/live_intelligence_ui.js` — replace
- `tests/test_live_prices.py` — **new**
- `tests/test_live_intelligence.py` — replace
- `tests/test_public_label_guard.py` — **new**
- `.github/workflows/lightweight-monitor.yml` — replace
- `.github/workflows/pages.yml` — replace
- `.github/workflows/nightly.yml` — replace

Do **not** upload `web/data.json`; Pages generates it.

## What v2B adds

1. Refreshes current daily prices for the active watch universe + XBI on every lightweight monitor run (keyless Yahoo adapter, best effort).
2. Adds price freshness metadata. Stale prices cannot present a current Market Attention score.
3. Suppresses relative-volume scoring while the US session is still open, avoiding the partial-volume bug seen in the friend's monitor.
4. Adds peer breadth context in addition to XBI, while excluding the stock itself from the breadth denominator.
5. Fixes `return_since_event`: the event baseline remains case-bound/frozen, but the latest price comes from the live shared price table rather than an old frozen historical capture.
6. Adds a public health block: SEC monitoring enabled/disabled, latest monitor/refresh, fresh/stale market-price tickers.
7. While empirical RUN-UP/HOLD gates are locked, the public payload downgrades `INVESTMENT_CANDIDATE` to `HIGH_RESEARCH_PRIORITY` / `עדיפות מחקר גבוהה`. Core heuristic calculations are left intact.
8. Pages is triggered automatically after a successful `lightweight-live-monitor`, so “מה השתנה?” no longer waits for the next daily Pages run.

## After upload

GitHub should automatically run CI and Pages. Then manually run `lightweight-live-monitor` once. With v2B, a successful monitor completion should automatically trigger another Pages deployment via `workflow_run`.

Check:

- CI green
- lightweight-live-monitor green
- automatic deploy-pages run appears after monitor completion
- Health banner says SEC active
- market-price dates are current/recent
- KOD shows the previous DAYBREAK event as already completed and the future BLA/PEAK chain separately
- no `מועמדת להשקעה` label while both empirical gates are locked; strongest heuristic label should be `עדיפות מחקר גבוהה`

## Deliberately not included yet

- automated cash/runway from SEC XBRL
- SEC sponsor→primary-ticker hardening
- PDUFA supersede/dedup rework
- options implied move / short interest / borrow fee
- LLM news interpretation

Those should be separate patches because they alter entity identity, lifecycle logic, or financial extraction and deserve their own tests.
