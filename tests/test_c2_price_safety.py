"""Bad prices must neither overwrite valid observations nor reach log/return math."""
from datetime import date, timedelta
import json
import math

import pytest

from mozes import db
from mozes.engine_v2 import market_setup_from_db, score_event
from mozes.ingest import prices


def rows(n=45, base=100):
    return [{'date': (date(2026,1,1) + timedelta(days=i)).isoformat(),
             'close': base + i, 'volume': 1000} for i in range(n)]


@pytest.mark.parametrize('writer', [db.store_prices, prices.store])
@pytest.mark.parametrize('field,bad', [('close', 0), ('close', -1), ('close', float('nan')),
    ('close', float('inf')), ('close', float('-inf')), ('close', 'bad'), ('close', True),
    ('volume', -1), ('volume', float('nan')), ('volume', float('inf')), ('volume', 'bad'), ('volume', True)])
def test_bad_batch_is_rejected_before_replacing_existing_prices(tmp_path, writer, field, bad):
    conn = db.connect(tmp_path / 'x.db')
    db.store_prices(conn, 'TEST', rows(), 'valid-original')
    original = db.load_prices(conn, 'TEST')
    batch = rows(2, 999)
    batch[-1][field] = bad
    with pytest.raises(ValueError):
        writer(conn, 'TEST', batch, 'bad-new-provider')
    assert db.load_prices(conn, 'TEST') == original


@pytest.mark.parametrize('field,bad', [('close', 0), ('close', -1), ('close', float('inf')),
                                    ('close', 'NaN'), ('close', 'bad'), ('volume', -1), ('volume', 'bad')])
def test_existing_bad_database_values_return_unavailable_market(tmp_path, field, bad):
    conn = db.connect(tmp_path / 'x.db')
    db.store_prices(conn, 'TEST', rows(), 'valid')
    with conn:
        conn.execute(f'UPDATE prices SET {field}=? WHERE ticker=? AND date=?',
                     (bad, 'TEST', rows()[-8]['date']))
    market = market_setup_from_db(conn, 'TEST', '2026-03-01')
    assert market['available'] is False and market['reason'] == 'invalid_stock_prices'
    assert 'returns' not in market and 'realized_vol_20d' not in market
    json.dumps(market, allow_nan=False)


def test_nonfinite_ratio_arithmetic_is_unavailable_not_inf_or_zero(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    series = rows()
    series[-1]['close'], series[-8]['close'] = 1e308, 1e-308
    db.store_prices(conn, 'TEST', series, 'finite-inputs')
    result = market_setup_from_db(conn, 'TEST', '2026-03-01')
    assert result['available'] is False and result['reason'] == 'invalid_market_arithmetic'
    json.dumps(result, allow_nan=False)


def test_invalid_benchmark_is_explicit_without_poisoning_stock_returns(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    db.store_prices(conn, 'TEST', rows(), 'stock')
    db.store_prices(conn, 'XBI', rows(base=200), 'benchmark')
    with conn:
        conn.execute("UPDATE prices SET close=0 WHERE ticker='XBI' AND date=?", (rows()[-8]['date'],))
    result = market_setup_from_db(conn, 'TEST', '2026-03-01')
    assert result['available'] is True
    assert result['relative_to_xbi'] == {} and result['benchmark_data_status'] == 'INVALID'
    assert result['returns']['T-7'] == pytest.approx(144 / 137 - 1)


def test_valid_prices_missing_volume_and_benchmark_days_keep_original_formulas(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    stock = rows()
    stock[-1]['volume'] = None
    stock[-2]['volume'] = 0
    bench = [r for i, r in enumerate(rows(base=200)) if i not in (10, 44)]
    db.store_prices(conn, 'TEST', stock, 'valid')
    db.store_prices(conn, 'XBI', bench, 'valid')
    out = market_setup_from_db(conn, 'TEST', '2026-03-01')
    assert out['available'] is True and out['last_close']['volume'] is None
    assert out['returns']['T-30'] == pytest.approx(144 / 114 - 1)
    lr = [math.log(stock[i]['close'] / stock[i-1]['close']) for i in range(25,45)]
    avg = sum(lr) / len(lr)
    expected = math.sqrt(sum((x-avg)**2 for x in lr)/(len(lr)-1)) * math.sqrt(252)
    assert out['realized_vol_20d'] == pytest.approx(expected)
    assert out['benchmark_as_of'] == bench[-1]['date']


def test_bad_import_preserves_frozen_historical_attachment(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    for ticker in ('TEST', 'XBI'):
        db.store_prices(conn, ticker, rows(), 'original')
    db.upsert_historical_case(conn, 'FROZEN', 'TEST', 'P3_TOPLINE', '2026-02-15')
    db.attach_historical_prices(conn, 'FROZEN', 'TEST', '2026-01-01', '2026-03-01', 'original')
    before = [tuple(r) for r in conn.execute('SELECT * FROM historical_case_prices')]
    with pytest.raises(ValueError, match='invalid_market_price'):
        prices.store(conn, 'TEST', [{'date': '2026-01-01', 'close': -1}], 'bad')
    assert [tuple(r) for r in conn.execute('SELECT * FROM historical_case_prices')] == before


def test_yahoo_zero_adjusted_close_does_not_silently_fallback_to_raw(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({'chart': {'result': [{'timestamp': [1767225600], 'indicators': {
                'quote': [{'close': [100], 'volume': [1000]}], 'adjclose': [{'adjclose': [0]}]}}]}}).encode()
    monkeypatch.setattr(prices.urllib.request, 'urlopen', lambda *args, **kwargs: Response())
    with pytest.raises(ValueError, match='invalid_market_price'):
        prices.fetch_yahoo_chart('TEST', '2026-01-01', '2026-01-02')


def test_raw_vs_adjusted_split_csv_preserves_exact_selected_values_and_frozen_case(tmp_path):
    raw = tmp_path / 'raw.csv'
    adjusted = tmp_path / 'adjusted.csv'
    raw.write_text('date,close,volume\n2026-01-01,100,1000\n2026-01-02,50,2000\n')
    adjusted.write_text('date,close,adj_close,volume\n2026-01-01,100,50,1000\n2026-01-02,50,50,2000\n')
    raw_rows, adj_rows = prices.load_csv(raw), prices.load_csv(adjusted)
    assert [x['close'] for x in raw_rows] == [100, 50]
    assert [x['close'] for x in adj_rows] == [50, 50]
    assert adj_rows[-1]['close'] / adj_rows[0]['close'] - 1 == 0
    conn = db.connect(tmp_path / 'x.db')
    db.store_prices(conn, 'TEST', raw_rows, 'csv-raw')
    db.upsert_historical_case(conn, 'FROZEN', 'TEST', 'P3_TOPLINE', '2026-01-03')
    db.attach_historical_prices(conn, 'FROZEN', 'TEST', '2026-01-01', '2026-01-02', 'csv-raw')
    frozen = [tuple(r) for r in conn.execute('SELECT * FROM historical_case_prices')]
    prices.store(conn, 'TEST', adj_rows, 'csv-adjusted')
    assert [tuple(r) for r in conn.execute('SELECT * FROM historical_case_prices')] == frozen


@pytest.mark.parametrize('adapter', ['csv', 'tiingo'])
def test_adapter_invalid_numeric_data_is_rejected_offline(tmp_path, monkeypatch, adapter):
    if adapter == 'csv':
        path = tmp_path / 'invalid.csv'
        path.write_text('date,close,volume\n2026-01-01,NaN,100\n')
        with pytest.raises(ValueError, match='invalid_market_price'):
            prices.load_csv(path)
    else:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'[{"date":"2026-01-01T00:00:00Z","adjClose":100,"adjVolume":-1}]'
        monkeypatch.setattr(prices, 'TIINGO_API_KEY', 'offline-fixture')
        monkeypatch.setattr(prices.urllib.request, 'urlopen', lambda *args, **kwargs: Response())
        with pytest.raises(ValueError, match='invalid_market_price'):
            prices.fetch_tiingo('TEST', '2026-01-01', '2026-01-02')


def test_active_event_scoring_reports_bad_market_without_rewriting_prices(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    db.store_prices(conn, 'TEST', rows(), 'original')
    with conn:
        conn.execute("UPDATE prices SET close=0 WHERE ticker='TEST' AND date=?", (rows()[-8]['date'],))
    before = db.load_prices(conn, 'TEST')
    result = score_event(conn, {'id': 'TEST-E', 'ticker': 'TEST', 'type': 'PDUFA_NME',
                               'chronology': [], 'features': {}}, '2026-03-01')
    assert result['market']['available'] is False
    assert result['market']['reason'] == 'invalid_stock_prices'
    assert 'returns' not in result['market']
    assert db.load_prices(conn, 'TEST') == before
    json.dumps(result, allow_nan=False)
