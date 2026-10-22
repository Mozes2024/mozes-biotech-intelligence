"""Best-effort refresh of current daily prices for the live research universe.

The historical pipeline remains case-bound and immutable. This module only updates the
shared `prices` staging table used for current market context in the live UI.
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

from datetime import datetime, timezone

from . import db
from .config import DB_PATH, TIINGO_API_KEY
from .ingest.prices import fetch_tiingo, fetch_yahoo_chart
from .radar import bootstrap_database
from .source_observability import Metrics


def refresh_live_prices(conn, *, today: date | None = None, lookback_days: int = 120,
                        fetcher=fetch_yahoo_chart, budget_seconds: float | None = None) -> dict:
    today = today or date.today()
    start = (today - timedelta(days=lookback_days)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    tickers = sorted({row["ticker"].upper() for row in db.watch_rows(conn) if row.get("ticker")} | {"XBI"})
    updated, errors, deferred = [], [], []
    latest = {}
    metrics = Metrics()
    started_all = time.monotonic()

    for ticker in tickers:
        if budget_seconds is not None and time.monotonic() - started_all >= budget_seconds:
            deferred = tickers[len(updated) + len(errors):]
            break
        started = time.monotonic()
        source = "yahoo-chart-keyless-live"
        try:
            rows = fetcher(ticker, start, end)
            if not rows:
                raise RuntimeError("provider returned no rows")
        except Exception as exc:  # best effort; do not break the primary monitor
            # Keyless Yahoo can block runner IPs; Tiingo is the keyed fallback when configured.
            if TIINGO_API_KEY:
                try:
                    rows = fetch_tiingo(ticker, start, end)
                    if not rows:
                        raise RuntimeError("provider returned no rows")
                    source = "tiingo-live"
                except Exception as fallback_exc:
                    metrics.record('yahoo', error=True, duration_ms=(time.monotonic() - started) * 1000)
                    errors.append({"ticker": ticker, "error": str(exc), "fallback_error": str(fallback_exc)})
                    continue
            else:
                metrics.record('yahoo', error=True, duration_ms=(time.monotonic() - started) * 1000)
                errors.append({"ticker": ticker, "error": str(exc)})
                continue
        db.store_prices(conn, ticker, rows, source)
        updated.append(ticker)
        latest[ticker] = rows[-1]["date"]
        metrics.record('tiingo' if source == "tiingo-live" else 'yahoo',
                       duration_ms=(time.monotonic() - started) * 1000)

    systemic_failure = bool(errors) and not updated
    bounded = bool(deferred)
    return {
        "status": ("PARTIAL" if systemic_failure else "INCOMPLETE" if errors
                   else "BOUNDED" if bounded else "OK"),
        "source_operations": metrics.snapshot(),
        "provider": "yahoo-chart-keyless",
        "start": start,
        "end": end,
        "requested": len(tickers),
        "updated": len(updated),
        "tickers": updated,
        "latest": latest,
        "errors": errors,
        "failed_tickers": [item["ticker"] for item in errors],
        "error_count": len(errors),
        "systemic_failure": systemic_failure,
        "bounded": bounded,
        "deferred_tickers": deferred,
        "budget_seconds": budget_seconds,
        "tiingo_fallback": bool(TIINGO_API_KEY),
        "best_effort": True,
    }


# Retry cadence per last completed refresh status (hours). A systemic failure
# retries sooner than a healthy daily cadence so an outage self-heals.
REFRESH_INTERVAL_HOURS = {"OK": 20, "INCOMPLETE": 20, "BOUNDED": 4, "PARTIAL": 3, "FAILED": 3}


def maybe_refresh_live_prices(conn, *, budget_seconds: float = 300) -> dict:
    """Gated best-effort price refresh for the hot lane.

    Enabled by default; MOZES_PRICE_REFRESH=0 opts out (e.g. tests, incident
    response). At most one refresh per interval; the 'prices' operation record
    is the gate and the health surface.
    """
    import os
    if os.environ.get("MOZES_PRICE_REFRESH") == "0":
        return {"status": "SKIPPED", "reason": "MOZES_PRICE_REFRESH disabled", "best_effort": True}
    row = conn.execute(
        "SELECT status, started_at FROM v2c_operation_runs "
        "WHERE operation='prices' AND status != 'RUNNING' ORDER BY run_id DESC LIMIT 1").fetchone()
    if row:
        try:
            age_hours = (datetime.now(timezone.utc)
                         - datetime.fromisoformat(row["started_at"])).total_seconds() / 3600
        except (TypeError, ValueError):
            age_hours = None
        interval = REFRESH_INTERVAL_HOURS.get(row["status"], 6)
        if age_hours is not None and age_hours < interval:
            return {"status": "SKIPPED", "reason": "recent_refresh",
                    "last_status": row["status"], "age_hours": round(age_hours, 2),
                    "interval_hours": interval, "best_effort": True}
    return refresh_live_prices(conn, budget_seconds=budget_seconds)


def main() -> int:
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    result = refresh_live_prices(conn)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # Market prices are an enrichment layer. A temporary Yahoo failure must not disable
    # SEC/CT.gov lifecycle monitoring or block Pages deployment.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
