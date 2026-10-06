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

## Operator setup

```bash
SEC_USER_AGENT="Name email@example.com"
# optional: MOZES_NTFY_URL=https://ntfy.sh/your-topic

MOZES_SEC_HOT=1 MOZES_PRIMARY_FEEDS=1 MOZES_ALERT_DISPATCH=1 \
  mozes hot-monitor --interval 60
```
