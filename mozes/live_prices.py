"""Best-effort refresh of current daily prices for the live research universe.

The historical pipeline remains case-bound and immutable. This module only updates the
shared `prices` staging table used for current market context in the live UI.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import db
from .config import DB_PATH
from .ingest.prices import fetch_yahoo_chart
from .radar import bootstrap_database


def refresh_live_prices(conn, *, today: date | None = None, lookback_days: int = 120, fetcher=fetch_yahoo_chart) -> dict:
    today = today or date.today()
    start = (today - timedelta(days=lookback_days)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    tickers = sorted({row["ticker"].upper() for row in db.watch_rows(conn) if row.get("ticker")} | {"XBI"})
    updated, errors = [], []
    latest = {}

    for ticker in tickers:
        try:
            rows = fetcher(ticker, start, end)
            if not rows:
                errors.append({"ticker": ticker, "error": "provider returned no rows"})
                continue
            db.store_prices(conn, ticker, rows, "yahoo-chart-keyless-live")
            updated.append(ticker)
            latest[ticker] = rows[-1]["date"]
        except Exception as exc:  # best effort; do not break the primary monitor
            errors.append({"ticker": ticker, "error": str(exc)})

    return {
        "provider": "yahoo-chart-keyless",
        "start": start,
        "end": end,
        "requested": len(tickers),
        "updated": len(updated),
        "tickers": updated,
        "latest": latest,
        "errors": errors,
        "best_effort": True,
    }


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
