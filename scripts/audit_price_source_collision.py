"""Offline collision reproduction. All writes are synthetic SQLite :memory: only."""
import json

from mozes import db
from mozes.ingest.prices import store


def audit():
    results = []
    for writer in (db.store_prices, store):
        conn = db.connect(':memory:')
        raw = [{'date': '2026-01-01', 'close': 100, 'volume': 1000},
               {'date': '2026-01-02', 'close': 100, 'volume': 1000}]
        for ticker in ('TEST', 'XBI'):
            writer(conn, ticker, raw, 'csv-raw')
        db.upsert_historical_case(conn, 'FROZEN', 'TEST', 'P3_TOPLINE', '2026-01-03')
        db.attach_historical_prices(conn, 'FROZEN', 'TEST', '2026-01-01', '2026-01-02', 'csv-raw')
        frozen = [tuple(r) for r in conn.execute('SELECT * FROM historical_case_prices')]
        before = db.load_prices(conn, 'TEST')
        writer(conn, 'TEST', [{'date': '2026-01-02', 'close': 50, 'volume': 2000}], 'tiingo-adjusted')
        after = db.load_prices(conn, 'TEST')
        results.append({'writer': writer.__module__ + '.' + writer.__name__, 'before': before,
                        'after': after, 'source_collision_confirmed': after[-1]['source'] != before[-1]['source'],
                        'mixed_basis_apparent_return': after[-1]['close'] / after[0]['close'] - 1,
                        'frozen_attachment_unchanged': frozen == [tuple(r) for r in conn.execute('SELECT * FROM historical_case_prices')]})
        conn.close()
    return results


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2))
