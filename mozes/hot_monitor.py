"""Always-on breaking-catalyst hot lane.

Designed for a small persistent worker (VPS/systemd). GitHub Actions remains for
CI, deep refresh, backfill and Pages snapshots — never as the alert transport.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

from . import db
from .alert_dispatch import dispatch_pending, sync_new_changes
from .config import DB_PATH
from .ir_registry import ir_entries
from .latency_metrics import summary as latency_summary
from .live_monitor import run_monitor
from .news_signals import poll_official_feeds
from .primary_feeds import fetch_feed, poll_fda_feeds, poll_nasdaq_halts, poll_wire_feeds
from .priority import priority_tickers
from .sec_http import enable_hot_mode


def _fetch(url):
    return fetch_feed(url, timeout=12)


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


def _step(conn, details, name, function):
    """Run one source in isolation so a single failing feed cannot drop the pass."""
    details["steps"].append(name)
    try:
        return function()
    except Exception as exc:  # noqa: BLE001 - every source failure must be contained
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        details["errors"][name] = f"{type(exc).__name__}: {exc}"[:240]
        return None


def _error_count(value):
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 1


def _watch_tickers(conn):
    return [row["ticker"] for row in db.watch_rows(conn)] or list(priority_tickers())


def run_hot_pass(conn, *, include_sec=True, include_wires=True, dispatch=True, fetch=None):
    """One bounded hot cycle: primary feeds → priority IR/SEC/news → outbox dispatch."""
    fetch = fetch or _fetch
    enable_hot_mode(True)
    started = datetime.now(timezone.utc).isoformat()
    details = {"started_at": started, "feeds": {}, "monitor": None, "alerts": None, "errors": {}, "steps": []}
    try:
        details["watch_seed"] = _step(conn, details, "watch_seed", lambda: seed_priority_watches(conn))
        details["feeds"]["fda"] = _step(conn, details, "fda", lambda: poll_fda_feeds(conn, fetch=fetch))
        if include_wires:
            details["feeds"]["wires"] = _step(conn, details, "wires", lambda: poll_wire_feeds(conn, fetch=fetch))

        def company_ir():
            hot = set(priority_tickers())
            issuers = [{"ticker": row["ticker"], "sponsor": row.get("company") or row["ticker"]}
                       for row in ir_entries() if row["ticker"] in hot]
            return poll_official_feeds(conn, issuers, fetch=fetch, now=datetime.now(timezone.utc))

        details["feeds"]["company_ir"] = _step(conn, details, "company_ir", company_ir)
        details["feeds"]["nasdaq_halts"] = _step(
            conn, details, "nasdaq_halts",
            lambda: poll_nasdaq_halts(conn, fetch=fetch, watch_tickers=_watch_tickers(conn)))
        if include_sec and os.environ.get("SEC_USER_AGENT"):
            details["monitor"] = _step(conn, details, "sec_monitor", lambda: run_monitor(
                conn, audit=False, ctgov_diff=False, sec=True, news=True,
                filings_per_company=6, priority_only=True))
        if dispatch:
            details["alerts"] = _step(conn, details, "alert_sync",
                                      lambda: sync_new_changes(conn, since_iso=started))
            details["alerts_flush"] = _step(conn, details, "alert_dispatch", lambda: dispatch_pending(conn))
        details["latency"] = _step(conn, details, "latency", lambda: latency_summary(conn, limit=200))
    finally:
        enable_hot_mode(False)
    feed_errors = sum(_error_count((value or {}).get("errors")) for value in details["feeds"].values()
                      if isinstance(value, dict))
    if details["errors"] and len(details["errors"]) == len(details["steps"]):
        details["status"] = "FAILED"
    elif details["errors"] or feed_errors:
        details["status"] = "PARTIAL"
    else:
        details["status"] = "OK"
    details["feed_errors"] = feed_errors
    return details


def run_worker(*, interval_seconds=60, once=False):
    conn = db.connect(os.environ.get("MOZES_DB_PATH", DB_PATH))
    interval = max(15, int(interval_seconds))
    deadline = time.monotonic()
    while True:
        result = run_hot_pass(conn)
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        if once:
            break
        # Fixed-cadence deadlines: a slow pass shortens the next sleep instead of drifting.
        deadline += interval
        now = time.monotonic()
        if deadline < now:
            deadline = now
        time.sleep(max(0.0, deadline - now))
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
