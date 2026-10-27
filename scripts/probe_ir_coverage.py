"""Read-only source coverage check using an isolated, disposable database.

No production database, Edge endpoint, alert delivery, credential or checkpoint
is opened. HTTP 403 is retained as a failure; approved alternate sources are
tested independently. The default scope is the three newly added fallbacks.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mozes import db
from mozes.hot_monitor import PassFetcher
from mozes.ir_registry import load_ir_registry
from mozes.news_signals import poll_official_feeds
from mozes.primary_feeds import fetch_feed


def inspect(tickers, *, fetch=None, now=None):
    registry = load_ir_registry()
    tickers = list(dict.fromkeys(tickers))
    if not tickers or len(tickers) > 40 or any(ticker not in registry for ticker in tickers):
        raise ValueError('select 1..40 configured issuer tickers')
    now = now or datetime.now(timezone.utc)
    fetch = fetch or PassFetcher(fetch=lambda url: fetch_feed(url, timeout=12, retries=0))
    rows = []
    with tempfile.TemporaryDirectory() as folder:
        conn = db.connect(Path(folder) / 'isolated-probe.db')
        try:
            for ticker in tickers:
                result = poll_official_feeds(conn, [{'ticker':ticker}], fetch=fetch, now=now)
                value = json.loads(conn.execute('SELECT value_json FROM monitor_observations WHERE observation_key=?',
                                               ('official_feed:'+ticker,)).fetchone()[0])
                rows.append({'ticker':ticker, 'primary_status':value['status'],
                             'primary_error':value.get('error') or value.get('feed_error'),
                             'wire_fallback':value.get('wire_fallback'),
                             'official_mirror':value.get('official_mirror'),
                             'historical_signals':result['signals_seen']})
            assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0
            assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
        finally:
            conn.close()
    return {'read_only':True, 'generated_at':now.isoformat(), 'sources':rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tickers', nargs='+', default=['TYRA','CYTK','TVTX'])
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = inspect(args.tickers)
    body = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body+'\n', encoding='utf-8')
    print(body)


if __name__ == '__main__':
    main()
