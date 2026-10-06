"""Always-on breaking-catalyst hot lane.

Designed for a small persistent worker (VPS/systemd). GitHub Actions remains for
CI, deep refresh, backfill and Pages snapshots — never as the alert transport.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .alert_dispatch import dispatch_pending, sync_new_changes
from .config import DB_PATH
from .ir_registry import ir_entries
from .latency_metrics import summary as latency_summary
from .live_monitor import run_monitor
from .news_signals import poll_official_feeds
from .primary_feeds import (
    FDA_FEEDS, NASDAQ_HALTS_FEED, WIRE_FEEDS, fetch_feed, poll_fda_feeds, poll_nasdaq_halts, poll_wire_feeds,
)
from .priority import priority_tickers
from .sec_http import enable_hot_mode


def _fetch(url):
    return fetch_feed(url, timeout=12)


class PassFetcher:
    """Per-pass cache with a parallel prefetch, so ~50 feeds cost one timeout, not fifty."""

    def __init__(self, fetch=_fetch, workers=8):
        self._fetch, self._workers, self._results = fetch, workers, {}

    def prefetch(self, urls):
        pending = [url for url in dict.fromkeys(urls) if url and url not in self._results]
        if not pending:
            return
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            for url, outcome in zip(pending, pool.map(self._capture, pending)):
                self._results[url] = outcome

    def _capture(self, url):
        try:
            return ("ok", self._fetch(url))
        except Exception as exc:  # noqa: BLE001 - replayed to the caller on __call__
            return ("error", exc)

    def __call__(self, url):
        if url not in self._results:
            self._results[url] = self._capture(url)
        kind, value = self._results[url]
        if kind == "error":
            raise value
        return value


def hot_urls():
    hot = set(priority_tickers())
    urls = [url for _name, url in FDA_FEEDS] + [url for _name, url in WIRE_FEEDS] + [NASDAQ_HALTS_FEED]
    for row in ir_entries():
        if row["ticker"] in hot:
            urls.append(row.get("feed_url") or row["site"])
    return urls


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
    if fetch is None:
        fetch = PassFetcher()
        fetch.prefetch(hot_urls())
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
            def sec_monitor():
                # The hot pass owns dispatch (and may be a silent warm-up); run_monitor must not send.
                previous = os.environ.get("MOZES_ALERT_DISPATCH")
                os.environ["MOZES_ALERT_DISPATCH"] = "0"
                try:
                    return run_monitor(conn, audit=False, ctgov_diff=False, sec=True, news=True,
                                       filings_per_company=6, priority_only=True)
                finally:
                    if previous is None:
                        os.environ.pop("MOZES_ALERT_DISPATCH", None)
                    else:
                        os.environ["MOZES_ALERT_DISPATCH"] = previous

            details["monitor"] = _step(conn, details, "sec_monitor", sec_monitor)
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


def heartbeat(status, *, url=None, fetch=None):
    """Dead-man's switch ping (e.g. healthchecks.io); a FAILED pass pings <url>/fail."""
    url = url if url is not None else os.environ.get("MOZES_HEARTBEAT_URL", "")
    if not url:
        return None
    target = url.rstrip("/") + "/fail" if status == "FAILED" else url
    try:
        (fetch or (lambda u: urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "MozesBiotechWorker/1.0"}), timeout=10).read()))(target)
        return target
    except Exception:  # noqa: BLE001 - monitoring must never stop the worker
        return None


def backup_db(conn, *, directory=None, keep=None, now=None):
    """Online SQLite backup into MOZES_BACKUP_DIR, keeping the newest N copies."""
    directory = directory or os.environ.get("MOZES_BACKUP_DIR", "")
    if not directory:
        return None
    keep = int(keep or os.environ.get("MOZES_BACKUP_KEEP", "8"))
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"mozes-hot-{stamp}.db"
    destination = sqlite3.connect(str(target))
    try:
        conn.backup(destination)
    finally:
        destination.close()
    for old in sorted(folder.glob("mozes-hot-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return str(target)


WARMUP_MARKER = "hot_lane:initialized"


def needs_warmup(conn):
    """True for an empty DB or one the hot lane has never polled (e.g. an existing Actions DB)."""
    return conn.execute("SELECT 1 FROM monitor_observations WHERE observation_key=?",
                        (WARMUP_MARKER,)).fetchone() is None


def mark_initialized(conn):
    from .live_monitor import observe
    observe(conn, WARMUP_MARKER, {"initialized_at": datetime.now(timezone.utc).isoformat()},
            source_type="hot_lane")


def run_hot_cycle(conn, **kwargs):
    """A hot pass that silently baselines the first time it runs against a DB."""
    warmup = needs_warmup(conn)
    result = run_hot_pass(conn, **{**kwargs, "dispatch": kwargs.get("dispatch", True) and not warmup})
    if warmup:
        result["warmup_silenced"] = silence_warmup(conn)
        mark_initialized(conn)
    return result


def silence_warmup(conn):
    """On an empty DB the first pass sees hours of backlog; it becomes the baseline, not pushes."""
    with conn:
        return conn.execute(
            "UPDATE alert_outbox SET status='dead', last_error='warm-up baseline' "
            "WHERE status IN ('pending','failed')").rowcount


def run_worker(*, interval_seconds=60, once=False, max_passes=None, sleep=time.sleep):
    conn = db.connect(os.environ.get("MOZES_DB_PATH", DB_PATH))
    interval = max(15, int(interval_seconds))
    backup_every = float(os.environ.get("MOZES_BACKUP_HOURS", "6")) * 3600
    last_backup = time.monotonic()
    deadline = time.monotonic()
    passes = 0
    while True:
        result = run_hot_cycle(conn)
        result["heartbeat"] = heartbeat(result.get("status"))
        if time.monotonic() - last_backup >= backup_every:
            try:
                result["backup"] = backup_db(conn)
            except (OSError, sqlite3.Error) as exc:
                result["backup_error"] = str(exc)[:200]
            last_backup = time.monotonic()
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
        passes += 1
        if once or (max_passes and passes >= max_passes):
            break
        # Fixed-cadence deadlines: a slow pass shortens the next sleep instead of drifting.
        deadline += interval
        now = time.monotonic()
        if deadline < now:
            deadline = now
        sleep(max(0.0, deadline - now))
    conn.close()
    return 0 if result.get("status") == "OK" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="MOZES breaking-catalyst hot monitor")
    parser.add_argument("--interval", type=int, default=60, help="seconds between passes")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-wires", action="store_true")
    parser.add_argument("--no-sec", action="store_true")
    parser.add_argument("--no-dispatch", action="store_true")
    parser.add_argument("--allow-partial", action="store_true", help="exit 0 when some sources failed")
    args = parser.parse_args(argv)
    if args.once or args.no_wires or args.no_sec or args.no_dispatch:
        conn = db.connect(os.environ.get("MOZES_DB_PATH", DB_PATH))
        result = run_hot_cycle(
            conn, include_sec=not args.no_sec, include_wires=not args.no_wires,
            dispatch=not args.no_dispatch)
        print(json.dumps(result, ensure_ascii=False, default=str))
        conn.close()
        if args.allow_partial and result.get("status") == "PARTIAL":
            return 0
        return 0 if result.get("status") == "OK" else 2
    return run_worker(interval_seconds=args.interval, once=False)


if __name__ == "__main__":
    raise SystemExit(main())
