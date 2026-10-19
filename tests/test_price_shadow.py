"""Offline boundary tests using synthetic snapshot files only."""
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from mozes import db, price_shadow, price_staging as stage
from mozes.ingest import prices
from mozes.ingest.price_provenance import prepare_import


def fixture(tmp_path, *, writer=db.store_prices, basis='raw', stock_close=100):
    legacy, staged = tmp_path / 'legacy.db', tmp_path / 'staged.db'
    old = db.connect(legacy)
    for ticker in ('TEST', 'XBI'):
        writer(old, ticker, [dict(date='2026-01-02', close=100, volume=1000),
                            dict(date='2026-01-05', close=101, volume=1100)], 'fixture')
    old.close()
    conn = stage.connect(staged, enabled=True)
    requests = []
    for ticker in ('TEST', 'XBI'):
        value = stock_close if ticker == 'TEST' else 100
        body = ('date,close,adj_close,volume,available_at,availability_reference\n'
                f'2026-01-02,100,{value},1000,2026-01-02T21:00:00Z,https://example.org/evidence\n'
                '2026-01-05,101,101,1100,2026-01-05T21:00:00Z,https://example.org/evidence\n').encode()
        batch = prepare_import('csv', body, run_id=ticker, instrument_id='isin:' + ticker,
                               ticker=ticker, captured_at='2026-01-06T20:00:00Z', vintage='v1',
                               price_basis=basis, volume_basis='raw', currency='USD', exchange='XNYS',
                               session_timezone='America/New_York', start='2026-01-02', end='2026-01-05',
                               price_field='adjusted_close' if basis != 'raw' else 'raw_close',
                               adjustment_policy='https://example.org/policy' if basis != 'raw' else None)
        stage.stage_imports(conn, [batch], enabled=True)
        requests.append(dict(ticker=ticker, selection=dict(instrument_id='isin:' + ticker,
            dates=['2026-01-02', '2026-01-05'], provider='csv', price_basis=basis, volume_basis='raw',
            currency='USD', vintage='v1', run_id=ticker, as_of='2026-01-07T00:00:00Z')))
    conn.close()
    return legacy, staged, *requests


def hashes(paths):
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


@pytest.mark.parametrize('writer', [db.store_prices, prices.store])
def test_equal_values_never_certify_legacy_provenance_and_retries_do_not_write(tmp_path, writer):
    legacy, staged, stock, benchmark = fixture(tmp_path, writer=writer)
    before = hashes((legacy, staged))
    first = price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)
    assert first == price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)
    assert first['numeric_equal'] and first['cutover'] == 'HOLD'
    assert first['legacy_written'] is first['staged_written'] is False
    assert hashes((legacy, staged)) == before


@pytest.mark.parametrize('basis,value', [('split_adjusted', 50), ('split_dividend_adjusted', 98),
                                         ('split_adjusted', 101)])
def test_split_dividend_and_correction_differences_are_explicit(tmp_path, basis, value):
    legacy, staged, stock, benchmark = fixture(tmp_path, basis=basis, stock_close=value)
    result = price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)
    assert not result['numeric_equal']
    assert result['series']['stock']['differences'] == [dict(date='2026-01-02', field='close', legacy=100, staged=value)]


def test_disabled_does_not_create_files(tmp_path, monkeypatch):
    monkeypatch.delenv('MOZES_PRICE_SHADOW_ENABLED', raising=False)
    with pytest.raises(stage.Disabled):
        price_shadow.compare(tmp_path / 'absent', tmp_path / 'other', {}, {})
    assert not list(tmp_path.iterdir())


def test_missing_files_cannot_bootstrap_or_modify_legacy(tmp_path):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    absent = tmp_path / 'absent.db'
    before = hashes((legacy, staged))
    with pytest.raises(FileNotFoundError):
        price_shadow.compare(legacy, absent, stock, benchmark, enabled=True)
    assert not absent.exists() and hashes((legacy, staged)) == before


def test_missing_and_extra_legacy_sessions_are_reported(tmp_path):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    with sqlite3.connect(legacy) as conn:
        conn.execute("DELETE FROM prices WHERE ticker='TEST' AND date='2026-01-02'")
        conn.execute("INSERT INTO prices VALUES ('TEST','2026-01-03',100,1000,'fixture')")
    result = price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)
    assert result['series']['stock']['missing_dates'] == ['2026-01-02']
    assert result['series']['stock']['extra_dates'] == ['2026-01-03']
    assert not result['numeric_equal']


@pytest.mark.parametrize('change,reason', [({'dates': ['2026-01-02']}, 'unmatched_trading_dates'),
    ({'dates': ['2026-01-02', '2026-01-02']}, 'stock:duplicate_trading_dates'),
    ({'dates': ['2026-01-02', '2026-01-06']}, 'stock:missing_trading_dates'),
    ({'vintage': 'v0'}, 'stock:no_compatible_run'),
    ({'as_of': '2026-01-05T00:00:00Z'}, 'stock:capture_after_cutoff')])
def test_alignment_dates_and_historical_vintage_refuse_without_fallback(tmp_path, change, reason):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    stock['selection'].update(change)
    before = hashes((legacy, staged))
    result = price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)
    assert not result['available'] and result['reason'] == reason
    assert hashes((legacy, staged)) == before


@pytest.mark.parametrize('field', ['run_id', 'as_of'])
def test_unpinned_requests_refused(tmp_path, field):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    stock['selection'].pop(field)
    with pytest.raises(ValueError, match='pinned_run_and_cutoff'):
        price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)


def test_ticker_mapping_and_same_file_refused(tmp_path):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    with pytest.raises(ValueError, match='separate_snapshot'):
        price_shadow.compare(legacy, legacy, stock, benchmark, enabled=True)
    stock['ticker'] = 'WRONG'
    with pytest.raises(ValueError, match='selected_ticker_mismatch'):
        price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)


def test_wal_dependency_is_refused_without_deletion(tmp_path):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    sidecar = tmp_path / 'legacy.db-wal'
    sidecar.write_bytes(b'not a standalone snapshot')
    with pytest.raises(ValueError, match='quiescent_checkpoint_copy'):
        price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)
    assert sidecar.read_bytes() == b'not a standalone snapshot'


def test_readonly_connection_cannot_mutate_or_initialize(tmp_path):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    conn = price_shadow._readonly(legacy)
    try:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            conn.execute('DELETE FROM prices')
    finally:
        conn.close()


def test_refused_selection_still_verifies_snapshot_stability(tmp_path, monkeypatch):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    stock['selection']['vintage'] = 'unavailable'
    select_pair = stage.select_pair

    def changed_snapshot(*args, **kwargs):
        result = select_pair(*args, **kwargs)
        assert not result['available']
        # An independent writer changes the synthetic input, never the comparator.
        with sqlite3.connect(legacy) as writer:
            writer.execute("UPDATE prices SET close=99 WHERE ticker='TEST'")
        return result

    monkeypatch.setattr(stage, 'select_pair', changed_snapshot)
    with pytest.raises(ValueError, match='snapshot_changed_during_comparison'):
        price_shadow.compare(legacy, staged, stock, benchmark, enabled=True)


def test_offline_cli_default_off_then_reports_pinned_readonly_result(tmp_path, monkeypatch, capsys):
    legacy, staged, stock, benchmark = fixture(tmp_path)
    request = tmp_path / 'request.json'
    request.write_text(json.dumps(dict(stock=stock, benchmark=benchmark)))
    loader = importlib.util.spec_from_file_location('compare_price_shadow',
        Path(__file__).resolve().parents[1] / 'scripts/compare_price_shadow.py')
    cli = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(cli)
    monkeypatch.setattr('sys.argv', ['compare_price_shadow', '--legacy', str(legacy),
                                   '--staging', str(staged), '--request', str(request)])
    before = hashes((legacy, staged))
    monkeypatch.delenv('MOZES_PRICE_SHADOW_ENABLED', raising=False)
    with pytest.raises(stage.Disabled):
        cli.main()
    monkeypatch.setenv('MOZES_PRICE_SHADOW_ENABLED', '1')
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result['numeric_equal'] and result['cutover'] == 'HOLD'
    assert result['snapshot_sha256'] == dict(zip(('legacy', 'staging'), before))
    assert hashes((legacy, staged)) == before


def test_cli_refuses_oversized_request_before_opening_databases(tmp_path, monkeypatch):
    request = tmp_path / 'request.json'
    request.write_bytes(b' ' * 65537)
    loader = importlib.util.spec_from_file_location('compare_price_shadow',
        Path(__file__).resolve().parents[1] / 'scripts/compare_price_shadow.py')
    cli = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(cli)
    missing = tmp_path / 'absent.db'
    monkeypatch.setattr('sys.argv', ['compare_price_shadow', '--legacy', str(missing),
                                   '--staging', str(missing), '--request', str(request)])
    with pytest.raises(ValueError, match='request_byte_budget'):
        cli.main()
    assert not missing.exists()
