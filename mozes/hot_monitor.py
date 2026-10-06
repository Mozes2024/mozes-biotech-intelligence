"""Always-on breaking-catalyst hot lane.

Designed for a small persistent worker (VPS/systemd). GitHub Actions remains for
CI, deep refresh, backfill and Pages snapshots — never as the alert transport.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from . import db
from .alert_dispatch import dispatch_pending, sync_new_changes
from .config import DB_PATH
from .ir_registry import ir_entries
from .latency_metrics import summary as latency_summary
from .live_monitor import run_monitor
from .news_signals import poll_official_feeds
from .primary_feeds import poll_fda_feeds, poll_nasdaq_halts, poll_wire_feeds
from .priority import priority_tickers
from .sec_http import enable_hot_mode


def _fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "MozesBiotechHotMonitor/1.0"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read(4_000_000)


def seed_priority_watches(conn):
    """Ensure every priority issuer is in watch_universe so SEC/halt polls cover them."""
    added = 0
    registry = {row["ticker"]: row for row in ir_entries()}
    for ticker in priority_tickers():
        row = registry.get(ticker) or {}
        before = conn.execute("SELECT 1 FROM watch_universe WHERE ticker=?", (ticker,)).fetchone()
        db.upsert_watch(conn, ticker, company=row.get("company"), source="priority_hot_lane")
        if not before:
            added += 1
    return {"seeded": len(priority_tickers()), "new": added}


def run_hot_pass(conn, *, include_sec=True, include_wires=True, dispatch=True):
    """One bounded hot cycle: primary feeds → priority IR/SEC/news → outbox dispatch."""
    enable_hot_mode(True)
    started = datetime.now(timezone.utc).isoformat()
    details = {"started_at": started, "feeds": {}, "monitor": None, "alerts": None}
    try:
        details["watch_seed"] = seed_priority_watches(conn)
        details["feeds"]["fda"] = poll_fda_feeds(conn, fetch=_fetch)
        if include_wires:
            details["feeds"]["wires"] = poll_wire_feeds(conn, fetch=_fetch)
        # Always poll configured company IR/RSS for the hot cohort.
        issuers = [{"ticker": row["ticker"], "sponsor": row.get("company") or row["ticker"]}
                   for row in ir_entries() if row["ticker"] in set(priority_tickers())]
        details["feeds"]["company_ir"] = poll_official_feeds(conn, issuers, fetch=_fetch, now=datetime.now(timezone.utc))
        watches = [row["ticker"] for row in db.watch_rows(conn)]
        details["feeds"]["nasdaq_halts"] = poll_nasdaq_halts(
            conn, fetch=_fetch, watch_tickers=watches or list(priority_tickers()))
        if include_sec and os.environ.get("SEC_USER_AGENT"):
            details["monitor"] = run_monitor(
                conn, audit=False, ctgov_diff=False, sec=True, news=True,
                filings_per_company=6, priority_only=True)
        if dispatch:
            details["alerts"] = sync_new_changes(conn, since_iso=started)
            details["alerts"]["flush"] = dispatch_pending(conn)
        details["latency"] = latency_summary(conn, limit=200)
        details["status"] = "OK"
    except Exception as exc:
        details["status"] = "FAILED"
        details["error"] = f"{type(exc).__name__}: {exc}"[:240]
    finally:
        enable_hot_mode(False)
    return details


def run_worker(*, interval_seconds=60, once=False):
    conn = db.connect(os.environ.get("MOZES_DB_PATH", DB_PATH))
    while True:
        result = run_hot_pass(conn)
        print(json.dumps(result, ensure_ascii=False, default=str))
        if once:
            break
        time.sleep(max(15, int(interval_seconds)))
    conn.close()
    return 0 if result.get("status") == "OK" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="MOZES breaking-catalyst hot monitor")
    parser.add_argument("--interval", type=int, default=60, help="seconds between passes")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-wires", action="store_true")
    parser.add_argument("--no-sec", action="store_true")
    parser.add_argument("--no-dispatch", action="store_true")
    args = parser.parse_args(argv)
    if args.once or args.no_wires or args.no_sec or args.no_dispatch:
        conn = db.connect(os.environ.get("MOZES_DB_PATH", DB_PATH))
        result = run_hot_pass(
            conn, include_sec=not args.no_sec, include_wires=not args.no_wires,
            dispatch=not args.no_dispatch)
        print(json.dumps(result, ensure_ascii=False, default=str))
        conn.close()
        return 0 if result.get("status") == "OK" else 2
    return run_worker(interval_seconds=args.interval, once=False)


if __name__ == "__main__":
    raise SystemExit(main())
