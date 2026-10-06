# Hot Lane / Breaking Catalyst Intelligence (v2F)

Independent reviews (ChatGPT, Claude, Grok — 2026-10-06) agreed the research
engine is strong but the operational path was a static verified snapshot, not a
real-time alert system. This note records what v2F adds **without** weakening
point-in-time gates, CT.gov discovery-only rules, or locked RUN-UP/HOLD unlocks.

## What changed

1. **Hot / deep concurrency split**
   - `lightweight-monitor.yml` → group `mozes-hot-monitor`, cron `7,22,37,52`.
   - `deep-monitor.yml` → group `mozes-deep-monitor` (research refresh only).
2. **Transactional alert outbox** (`alert_outbox` / `alert_deliveries`) with
   ntfy, webhook, or log adapters. Pages remains a consumer.
3. **SEC hot TTL** — submissions cache 60s when `MOZES_SEC_HOT=1` / hot mode.
4. **Material discovery escalation** — minutes-scale retries for material
   headlines instead of a flat 24h cooldown; IR HTML fallback when no RSS.
5. **Primary feeds** — FDA RSS, wire RSS hooks, Nasdaq trade-halt RSS.
6. **Latency ledger** — publication → first seen → queued → sent timestamps.
7. **CT.gov imminence fields** — `primary_completion_type` and
   `results_first_posted` diffs (still never a readout date).
8. **Deterministic outcome polarity** for Stage-1 text (uncalibrated).
9. **`mozes hot-monitor` / `mozes dispatch-alerts`** for a persistent worker.
10. **Expanded hot watchlist (~40)** with official IR sites and verified RSS URLs.

## What was intentionally not changed

- Evidence / explosiveness scores are still uncalibrated and not buy signals.
- OOS gates remain locked (`oos_n=0` until a prospective contract exists).
- ClinicalTrials.gov remains discovery-only.
- No MNPI / non-public sources.
- Telegram/email push channels are deferred (outbox supports ntfy/webhook/log).

## v2G hardening (code review follow-up)

| Concern | Resolution |
| --- | --- |
| One failing source dropped the whole pass | `run_hot_pass` isolates each step; status `OK` / `PARTIAL` / `FAILED` (all failed) |
| Rows stuck in `sending` after a crash | 2-minute lease in `send_after`; `recover_stale_sending` → `failed` or `dead` |
| Guessed feed URLs | `scripts/capture_feed_fixtures.py` verifies status + XML; samples in `tests/fixtures/feeds/` |
| Nasdaq halt parser | `ndaq:IssueSymbol` / `ReasonCode` / `HaltDate`+`HaltTime` (ET); no `<link>` in real items |
| P1 noise | P1 only for watched tickers; no ticker → P3 (not queued at default `MOZES_ALERT_MIN_PRIORITY=P2`) |
| Classifier errors | Hedge/negation guards, mixed on conflict; corpus regression test |
| Duplicate push from Actions and worker | Push secrets removed from workflows; `push_allowed()` blocks push inside Actions |
| Same story from several sources | `alert_links`: same ticker, 30 min, headline similarity ≥ 0.55 |

Feed status on 2026-10-06 from a GitHub runner (`feed-probe` workflow; re-run it to re-verify):

| Feed | Status |
| --- | --- |
| FDA Press Releases, What's New: Drugs, Vaccines/Blood/Biologics | 200, valid RSS |
| Business Wire Health, Clinical Trials | 200, valid RSS |
| PR Newswire Biotechnology, All Health | 200, valid RSS |
| GlobeNewswire Biotechnology, Clinical Study | 200, valid RSS (Windows Python may need an up-to-date CA bundle) |
| Nasdaq trade halts | 200, `ndaq:` namespace |

fda.gov answers 401 to some networks, so FDA fixtures come from the runner artifact.

Halt codes: `T1 T2 T3 T12 H10 H11` → `nasdaq_halt_signal` (critical, P1 when watched);
`LUDP LUDS M` → `nasdaq_volatility_pause` (high, P2). Other codes are ignored.

## Operator setup

```bash
SEC_USER_AGENT="Name email@example.com"
# optional: MOZES_NTFY_URL=https://ntfy.sh/your-topic

MOZES_SEC_HOT=1 MOZES_PRIMARY_FEEDS=1 MOZES_ALERT_DISPATCH=1 \
  mozes hot-monitor --interval 60
```
