"""Synthetic provenance contracts; never use production data or network feeds."""
import json
import hashlib
import sqlite3

import pytest

from mozes import db
from mozes.ingest import prices


@pytest.mark.parametrize('writer', [db.store_prices, prices.store])
@pytest.mark.parametrize('scenario,replacement', [('split', 50), ('dividend', 98), ('revision', 101)])
def test_legacy_writers_reproduce_silent_valid_price_collision(writer, scenario, replacement):
    conn = db.connect(':memory:')
    writer(conn, 'TEST', [{'date': '2026-01-02', 'close': 100}], 'csv-raw-v1')
    writer(conn, 'TEST', [{'date': '2026-01-02', 'close': replacement}], scenario + '-v2')
    rows = db.load_prices(conn, 'TEST')
    # This intentionally documents the legacy boundary, which is NOT rolled out here.
    assert len(rows) == 1 and rows[0]['close'] == replacement
    assert rows[0]['source'] == scenario + '-v2'


def api():
    from mozes import price_staging
    from mozes.ingest import price_provenance
    return price_staging, price_provenance


def batch(run='r1', provider='csv', body=None, **changes):
    _, adapter = api()
    fields = dict(run_id=run, instrument_id='isin:US0000000001', ticker='TEST',
                  captured_at='2026-01-05T20:00:00Z', vintage='2026-01-05',
                  price_basis='raw', volume_basis='raw', currency='USD',
                  exchange='XNYS', session_timezone='America/New_York',
                  start='2026-01-02', end='2026-01-05', adjustment_policy=None)
    fields.update(changes)
    body = body if body is not None else b'date,close,volume\n2026-01-02,100,1000\n2026-01-05,101,1100\n'
    return adapter.prepare_import(provider, body, **fields)


def staged():
    stage, _ = api()
    conn = stage.connect(':memory:', enabled=True)
    return stage, conn


def spec(**changes):
    fields = dict(instrument_id='isin:US0000000001', dates=['2026-01-02', '2026-01-05'],
                  provider='csv', price_basis='raw', volume_basis='raw', currency='USD',
                  vintage='2026-01-05')
    fields.update(changes)
    return fields


def test_staging_and_selection_are_disabled_by_default(monkeypatch, tmp_path):
    stage, _ = api()
    monkeypatch.delenv('MOZES_PRICE_STAGING_ENABLED', raising=False)
    monkeypatch.delenv('MOZES_PRICE_SELECTION_ENABLED', raising=False)
    path = tmp_path / 'not-created.sqlite'
    with pytest.raises(stage.Disabled):
        stage.connect(path)
    assert not path.exists()
    _, conn = staged()
    with pytest.raises(stage.Disabled):
        stage.stage_imports(conn, [batch()])
    with pytest.raises(stage.Disabled):
        stage.select_series(conn, **spec())


def test_append_only_runs_idempotency_and_conflicts_are_atomic():
    stage, conn = staged()
    original = batch()
    stage.stage_imports(conn, [original], enabled=True)
    before = list(conn.iterdump())
    stage.stage_imports(conn, [original], enabled=True)
    assert list(conn.iterdump()) == before
    revised = batch(body=b'date,close\n2026-01-02,50\n2026-01-05,50\n')
    with pytest.raises(stage.Conflict):
        stage.stage_imports(conn, [batch('new'), revised], enabled=True)
    assert list(conn.iterdump()) == before
    for sql in ('UPDATE staged_price_runs SET payload=payload',
                'DELETE FROM staged_price_observations',
                'INSERT OR REPLACE INTO staged_price_runs SELECT * FROM staged_price_runs'):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)
    assert list(conn.iterdump()) == before


def test_provider_basis_vintage_collisions_remain_distinct_and_never_splice():
    stage, conn = staged()
    stage.stage_imports(conn, [batch(), batch('adjusted', price_basis='split_adjusted',
        adjustment_policy='https://example.org/split-policy',
        body=b'date,close,adj_close,volume\n2026-01-02,100,50,1000\n2026-01-05,101,50.5,1100\n'),
        batch('revised', vintage='2026-01-06', captured_at='2026-01-06T20:00:00Z',
              body=b'date,close\n2026-01-02,99\n2026-01-05,100\n')], enabled=True)
    raw = stage.select_series(conn, **spec(), enabled=True)
    adj = stage.select_series(conn, **spec(price_basis='split_adjusted'), enabled=True)
    revision = stage.select_series(conn, **spec(vintage='2026-01-06'), enabled=True)
    assert [r['close'] for r in raw['rows']] == [100, 101]
    assert [r['close'] for r in adj['rows']] == [50, 50.5]
    assert [r['close'] for r in revision['rows']] == [99, 100]
    assert raw['run']['response_hash'] != revision['run']['response_hash']
    assert raw['run']['run_id'] == 'r1' and adj['run']['run_id'] == 'adjusted'


def yahoo_body(adjusted):
    return json.dumps({'chart': {'result': [{'meta': {'currency': 'USD'},
        'timestamp': [1767373200, 1767632400], 'indicators': {
            'quote': [{'close': [100, 50], 'volume': [1000, 2000]}],
            'adjclose': [{'adjclose': adjusted}]}}]}}).encode()


def test_yahoo_missing_adjusted_values_never_fall_back_to_raw():
    stage, conn = staged()
    prepared = batch(provider='yahoo', body=yahoo_body([50, None]),
                     price_basis='split_dividend_adjusted',
                     adjustment_policy='https://example.org/declared-yahoo-policy')
    stage.stage_imports(conn, [prepared], enabled=True)
    out = stage.select_series(conn, **spec(provider='yahoo', price_basis='split_dividend_adjusted'), enabled=True)
    assert not out['available'] and out['reason'] == 'missing_selected_close'
    assert prepared['rows'][1]['raw_close'] == 50
    assert prepared['rows'][1]['adjusted_close'] is None


@pytest.mark.parametrize('field', ['price_basis', 'volume_basis', 'currency', 'vintage'])
def test_unknown_provenance_cannot_be_selected(field):
    stage, conn = staged()
    stage.stage_imports(conn, [batch(**{field: 'unknown'})], enabled=True)
    out = stage.select_series(conn, **spec(**{field: 'unknown'}), enabled=True)
    assert not out['available'] and out['reason'] == 'unknown_provenance'


def test_future_capture_and_missing_availability_do_not_invent_historical_provenance():
    stage, conn = staged()
    stage.stage_imports(conn, [batch()], enabled=True)
    out = stage.select_series(conn, **spec(), as_of='2026-01-03T21:00:00Z', enabled=True)
    assert not out['available'] and out['reason'] == 'capture_after_cutoff'
    out = stage.select_series(conn, **spec(), as_of='2026-01-06T21:00:00Z', enabled=True)
    assert not out['available'] and out['reason'] == 'unproven_availability'


def test_missing_benchmark_dates_refuses_pair_and_manifest():
    stage, conn = staged()
    stage.stage_imports(conn, [batch(), batch('xbi', instrument_id='isin:US78464A8707', ticker='XBI',
        body=b'date,close,volume\n2026-01-02,200,2000\n')], enabled=True)
    stock, bench = spec(), spec(instrument_id='isin:US78464A8707')
    result = stage.select_pair(conn, stock, bench, enabled=True)
    assert not result['available'] and result['reason'] == 'benchmark:missing_trading_dates'
    with pytest.raises(stage.Unavailable):
        stage.freeze_attachment(conn, 'a1', 'future-case', stock, bench, enabled=True)
    assert conn.execute('SELECT count(*) FROM staged_price_attachments').fetchone()[0] == 0


def test_future_manifest_is_immutable_and_legacy_database_is_untouched():
    stage, conn = staged()
    stage.stage_imports(conn, [batch(), batch('xbi', instrument_id='isin:US78464A8707', ticker='XBI')], enabled=True)
    stock, bench = spec(), spec(instrument_id='isin:US78464A8707')
    manifest = stage.freeze_attachment(conn, 'a1', 'future-case', stock, bench, enabled=True)
    assert manifest == stage.freeze_attachment(conn, 'a1', 'future-case', stock, bench, enabled=True)
    assert manifest['stock']['run']['run_id'] == 'r1'
    assert manifest['benchmark']['run']['run_id'] == 'xbi'
    with pytest.raises(stage.Conflict):
        stage.freeze_attachment(conn, 'a1', 'other-case', stock, bench, enabled=True)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('DELETE FROM staged_price_attachments')


@pytest.mark.parametrize('bad', [0, -1, 'NaN', 'Infinity', 'oops'])
def test_invalid_values_are_rejected_before_any_staging_write(bad):
    stage, conn = staged()
    with pytest.raises(ValueError):
        prepared = batch(body=f'date,close\n2026-01-02,{bad}\n'.encode())
        stage.stage_imports(conn, [prepared], enabled=True)
    assert conn.execute('SELECT count(*) FROM staged_price_runs').fetchone()[0] == 0


def test_tiingo_keeps_raw_and_adjusted_price_and_volume_without_provider_metadata():
    stage, conn = staged()
    body = json.dumps([{'date': '2026-01-02T00:00:00Z', 'close': 100, 'volume': 1000,
                        'adjClose': 49, 'adjVolume': 2000, 'token': 'NEVER-PERSIST',
                        'url': 'https://example.org/?token=NEVER-PERSIST'},
                       {'date': '2026-01-05T00:00:00Z', 'close': 50, 'volume': 2000,
                        'adjClose': 49.5, 'adjVolume': 2000}]).encode()
    prepared = batch(provider='tiingo', body=body, price_basis='split_dividend_adjusted',
                     volume_basis='split_adjusted', adjustment_policy='https://example.org/tiingo-policy')
    stage.stage_imports(conn, [prepared], enabled=True)
    out = stage.select_series(conn, **spec(provider='tiingo', price_basis='split_dividend_adjusted',
                                         volume_basis='split_adjusted'), enabled=True)
    assert out['available']
    assert [r['close'] for r in out['rows']] == [49, 49.5]
    assert out['rows'][0]['raw_close'] == 100 and out['rows'][0]['volume'] == 2000
    assert 'NEVER-PERSIST' not in '\n'.join(conn.iterdump())
    assert out['run']['response_hash'] == hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize('ref', ['https://example.org/policy?token=secret',
                               'https://user:password@example.org/policy',
                               'https://example.org/policy#secret', 'file:///private'])
def test_token_bearing_or_private_references_are_rejected(ref):
    with pytest.raises(ValueError, match='unsafe_provenance_reference'):
        batch(price_basis='split_adjusted', adjustment_policy=ref)


def test_adjusted_csv_never_uses_raw_cells_as_adjusted_fallback():
    stage, conn = staged()
    prepared = batch(price_basis='split_adjusted', adjustment_policy='https://example.org/policy',
                     body=b'date,close,adj_close\n2026-01-02,100,50\n2026-01-05,101,\n')
    stage.stage_imports(conn, [prepared], enabled=True)
    out = stage.select_series(conn, **spec(price_basis='split_adjusted'), enabled=True)
    assert out == {'available': False, 'reason': 'missing_selected_close'}


def test_csv_defaults_preserve_unknown_adjustment_conventions():
    _, adapter = api()
    fields = batch()['run']
    prepared = adapter.prepare_import('csv', b'date,close\n2026-01-02,100\n',
        **{k: fields[k] for k in ('run_id', 'instrument_id', 'ticker', 'captured_at', 'vintage', 'start', 'end')})
    assert prepared['run']['price_basis'] == prepared['run']['volume_basis'] == 'unknown'
    assert prepared['run']['currency'] == 'unknown'


def test_duplicate_sessions_are_rejected_not_last_row_wins():
    with pytest.raises(ValueError, match='duplicate_or_out_of_range_session'):
        batch(body=b'date,close\n2026-01-02,100\n2026-01-02,50\n')


def test_observation_replace_cannot_bypass_composite_identity_trigger():
    stage, conn = staged()
    stage.stage_imports(conn, [batch()], enabled=True)
    row = conn.execute('SELECT * FROM staged_price_observations LIMIT 1').fetchone()
    with pytest.raises(sqlite3.IntegrityError, match='immutable_price_staging'):
        conn.execute('INSERT OR REPLACE INTO staged_price_observations VALUES (?,?,?,?)',
                     ('new-id', row['run_id'], row['trading_date'], row['payload']))


def test_latest_partial_capture_does_not_silently_fallback_or_splice():
    stage, conn = staged()
    stage.stage_imports(conn, [batch(), batch('partial', captured_at='2026-01-05T21:00:00Z',
        body=b'date,close\n2026-01-05,102\n')], enabled=True)
    assert stage.select_series(conn, **spec(), enabled=True)['reason'] == 'missing_trading_dates'
    pinned = stage.select_series(conn, **spec(run_id='r1'), enabled=True)
    assert pinned['available'] and pinned['rows'][-1]['close'] == 101


def test_provider_series_cannot_supply_each_others_missing_days():
    stage, conn = staged()
    stage.stage_imports(conn, [batch(body=b'date,close\n2026-01-02,100\n'),
        batch('other-provider', provider='tiingo', body=b'[{"date":"2026-01-05","close":101}]')], enabled=True)
    for provider in ('csv', 'tiingo'):
        assert stage.select_series(conn, **spec(provider=provider), enabled=True)['reason'] == 'missing_trading_dates'


@pytest.mark.parametrize('as_of', ['2026-01-05', '2026-01-05T20:00:00', 'bad', '2026-02-30T00:00:00Z'])
def test_ambiguous_or_malformed_historical_cutoff_fails_closed(as_of):
    stage, conn = staged()
    stage.stage_imports(conn, [batch()], enabled=True)
    assert stage.select_series(conn, **spec(), as_of=as_of, enabled=True)['reason'] == 'invalid_date_or_cutoff'


def test_explicit_availability_is_separate_from_capture_and_preserved_in_manifest():
    stage, conn = staged()
    body = (b'date,close,available_at,availability_reference\n'
            b'2026-01-02,100,2026-01-02T21:05:00Z,https://example.org/evidence/1\n'
            b'2026-01-05,101,2026-01-05T21:05:00Z,https://example.org/evidence/2\n')
    stage.stage_imports(conn, [batch(body=body, captured_at='2026-01-05T21:10:00Z')], enabled=True)
    out = stage.select_series(conn, **spec(), as_of='2026-01-05T21:15:00Z', enabled=True)
    assert out['available']
    assert out['rows'][-1]['available_at'].startswith('2026-01-05T21:05:00')
    assert out['run']['captured_at'].startswith('2026-01-05T21:10:00')
    early = stage.select_series(conn, **spec(), as_of='2026-01-05T20:59:59Z', enabled=True)
    assert early['available'] is False


def test_different_stock_and_benchmark_providers_are_independently_traceable():
    stage, conn = staged()
    stage.stage_imports(conn, [batch(), batch('xbi', provider='tiingo', instrument_id='isin:US78464A8707',
        ticker='XBI', body=b'[{"date":"2026-01-02","close":200},{"date":"2026-01-05","close":202}]')], enabled=True)
    result = stage.select_pair(conn, spec(), spec(provider='tiingo', instrument_id='isin:US78464A8707'), enabled=True)
    assert result['available']
    assert result['stock']['run']['provider'] == 'csv'
    assert result['benchmark']['run']['provider'] == 'tiingo'
    assert [r['date'] for r in result['stock']['rows']] == [r['date'] for r in result['benchmark']['rows']]


def test_legacy_file_and_frozen_history_remain_byte_for_byte_unchanged(tmp_path):
    stage, _ = api()
    path = tmp_path / 'legacy.db'
    conn = db.connect(path)
    for ticker in ('TEST', 'XBI'):
        prices.store(conn, ticker, [{'date': '2026-01-02', 'close': 100}], 'original')
    db.upsert_historical_case(conn, 'FROZEN', 'TEST', 'P3_TOPLINE', '2026-01-05')
    db.attach_historical_prices(conn, 'FROZEN', 'TEST', '2026-01-02', '2026-01-02', 'original')
    db.archive_source(conn, 'source-id', 'https://example.org/filing', 'SEC',
                      '2026-01-01', '2026-01-02', 'immutable evidence')
    db.set_validation(conn, 'runup', False, 0, 150)
    conn.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match='separate_staging_database_required'):
        stage.connect(path, enabled=True)
    stage_conn = stage.connect(tmp_path / 'separate.db', enabled=True)
    stage.stage_imports(stage_conn, [batch(), batch('xbi', ticker='XBI', instrument_id='isin:US78464A8707')], enabled=True)
    stage.freeze_attachment(stage_conn, 'new-attachment', 'new-case', spec(),
                            spec(instrument_id='isin:US78464A8707'), enabled=True)
    stage_conn.close()
    assert path.read_bytes() == before


def test_import_and_selection_budgets_fail_closed(monkeypatch):
    stage, adapter = api()
    _, conn = staged()
    monkeypatch.setattr(adapter, 'MAX_RESPONSE_BYTES', 8)
    with pytest.raises(ValueError, match='response_byte_budget'):
        batch()
    monkeypatch.setattr(adapter, 'MAX_RESPONSE_BYTES', 8 * 1024 * 1024)
    prepared = batch()
    monkeypatch.setattr(stage, 'MAX_OBSERVATIONS', 1)
    with pytest.raises(ValueError, match='staging_storage_budget'):
        stage.stage_imports(conn, [prepared], enabled=True)
    assert conn.execute('SELECT count(*) FROM staged_price_runs').fetchone()[0] == 0
    monkeypatch.setattr(stage, 'MAX_OBSERVATIONS', 100)
    stage.stage_imports(conn, [prepared], enabled=True)
    monkeypatch.setattr(stage, 'MAX_ROWS', 1)
    assert stage.select_series(conn, **spec(), enabled=True)['reason'] == 'invalid_trading_dates'


def test_yahoo_quote_field_basis_must_be_declared_not_inferred_from_its_name():
    stage, conn = staged()
    stage.stage_imports(conn, [batch(provider='yahoo', body=yahoo_body([49, 49.5]),
        price_field='raw_close', price_basis='split_adjusted',
        adjustment_policy='https://example.org/quote-split-policy')], enabled=True)
    selected = stage.select_series(conn, **spec(provider='yahoo', price_basis='split_adjusted'), enabled=True)
    assert [r['close'] for r in selected['rows']] == [100, 50]
    assert selected['run']['field_basis']['raw_close'] == 'split_adjusted'
    assert selected['run']['field_basis']['adjusted_close'] == 'unknown'


def test_dividend_revision_preserves_both_vintages_and_original_manifest():
    stage, conn = staged()
    policy = 'https://example.org/dividend-policy'
    first = batch(price_basis='split_dividend_adjusted', adjustment_policy=policy,
                  body=b'date,close,adj_close\n2026-01-02,100,98\n2026-01-05,101,99\n')
    benchmark = batch('xbi', ticker='XBI', instrument_id='isin:US78464A8707',
                      price_basis='split_dividend_adjusted', adjustment_policy=policy,
                      body=b'date,close,adj_close\n2026-01-02,200,198\n2026-01-05,201,199\n')
    stage.stage_imports(conn, [first, benchmark], enabled=True)
    stock, bench = spec(price_basis='split_dividend_adjusted'), spec(
        instrument_id='isin:US78464A8707', price_basis='split_dividend_adjusted')
    original = stage.freeze_attachment(conn, 'frozen-new', 'case-new', stock, bench, enabled=True)
    frozen = conn.execute('SELECT payload FROM staged_price_attachments').fetchone()[0]
    stage.stage_imports(conn, [batch('new-vintage', vintage='2026-01-06',
        captured_at='2026-01-06T20:00:00Z', price_basis='split_dividend_adjusted', adjustment_policy=policy,
        body=b'date,close,adj_close\n2026-01-02,100,97\n2026-01-05,101,98\n')], enabled=True)
    assert original['stock']['rows'][0]['close'] == 98
    assert conn.execute('SELECT payload FROM staged_price_attachments').fetchone()[0] == frozen
    revised = stage.select_series(conn, **spec(price_basis='split_dividend_adjusted', vintage='2026-01-06'), enabled=True)
    assert revised['rows'][0]['close'] == 97
    assert stage.select_series(conn, **stock, enabled=True)['rows'][0]['close'] == 98


def test_retry_after_reopen_and_second_connection_preserves_run_identity(tmp_path):
    stage, _ = api()
    path = tmp_path / 'staging.db'
    conn = stage.connect(path, enabled=True)
    stage.stage_imports(conn, [batch()], enabled=True)
    conn.close()
    conn = stage.connect(path, enabled=True)
    second = stage.connect(path, enabled=True)
    stage.stage_imports(second, [batch()], enabled=True)
    assert conn.execute('SELECT count(*) FROM staged_price_runs').fetchone()[0] == 1
    assert conn.execute('SELECT count(*) FROM staged_price_observations').fetchone()[0] == 2
    changed = batch(body=b'date,close\n2026-01-02,90\n')
    with pytest.raises(stage.Conflict):
        stage.stage_imports(second, [changed], enabled=True)
    assert stage.select_series(conn, **spec(), enabled=True)['rows'][0]['close'] == 100
    second.close()
    conn.close()


def test_manifest_budget_refusal_is_atomic(monkeypatch):
    stage, conn = staged()
    stage.stage_imports(conn, [batch(), batch('xbi', instrument_id='isin:US78464A8707', ticker='XBI')], enabled=True)
    monkeypatch.setattr(stage, 'MAX_MANIFEST_BYTES', 1)
    with pytest.raises(ValueError, match='attachment_manifest_budget'):
        stage.freeze_attachment(conn, 'a1', 'case', spec(), spec(instrument_id='isin:US78464A8707'), enabled=True)
    assert conn.execute('SELECT count(*) FROM staged_price_attachments').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM staged_price_runs').fetchone()[0] == 2


@pytest.mark.parametrize('mutation', ['extra_metadata', 'currency', 'number', 'field_basis'])
def test_direct_write_boundary_revalidates_untrusted_prepared_batches(mutation):
    stage, conn = staged()
    prepared = batch()
    if mutation == 'extra_metadata':
        prepared['run']['request_url'] = 'https://example.org/?token=secret'
    elif mutation == 'currency':
        prepared['run']['currency'] = 'bad?token=secret'
    elif mutation == 'field_basis':
        prepared['run']['field_basis']['raw_close'] = 'unknown'
    else:
        prepared['rows'][0]['raw_close'] = '100'
    with pytest.raises(ValueError):
        stage.stage_imports(conn, [prepared], enabled=True)
    assert conn.execute('SELECT count(*) FROM staged_price_runs').fetchone()[0] == 0


def test_complete_split_adjusted_window_avoids_fabricated_cross_basis_return():
    stage, conn = staged()
    stage.stage_imports(conn, [batch(body=b'date,close\n2026-01-02,100\n2026-01-05,50\n'),
        batch('adjusted', price_basis='split_adjusted', adjustment_policy='https://example.org/split',
              body=b'date,close,adj_close\n2026-01-02,100,50\n2026-01-05,50,50\n')], enabled=True)
    raw = stage.select_series(conn, **spec(), enabled=True)
    adjusted = stage.select_series(conn, **spec(price_basis='split_adjusted'), enabled=True)
    assert raw['rows'][-1]['close'] / raw['rows'][0]['close'] - 1 == -0.5
    assert adjusted['rows'][-1]['close'] / adjusted['rows'][0]['close'] - 1 == 0
    assert raw['run']['run_id'] != adjusted['run']['run_id']


@pytest.mark.parametrize('stock_change,bench_change,reason', [
    ({}, {'dates': ['2026-01-02']}, 'unmatched_trading_dates'),
    ({}, {'as_of': '2026-01-05'}, 'benchmark:invalid_date_or_cutoff'),
])
def test_pair_refuses_different_dates_or_ambiguous_cutoffs(stock_change, bench_change, reason):
    stage, conn = staged()
    stage.stage_imports(conn, [batch(), batch('xbi', instrument_id='isin:US78464A8707', ticker='XBI')], enabled=True)
    out = stage.select_pair(conn, spec(**stock_change), spec(instrument_id='isin:US78464A8707', **bench_change), enabled=True)
    assert out == {'available': False, 'reason': reason}


def test_caller_transaction_is_not_committed_and_can_rollback_staging():
    stage, conn = staged()
    conn.execute('BEGIN')
    stage.stage_imports(conn, [batch()], enabled=True)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute('SELECT count(*) FROM staged_price_runs').fetchone()[0] == 0


def test_offline_cli_requires_gate_and_imports_without_touching_legacy(tmp_path, monkeypatch, capsys):
    from scripts import stage_price_response
    stage, _ = api()
    source = tmp_path / 'source.csv'
    source.write_bytes(b'date,close\n2026-01-02,100\n')
    declaration = tmp_path / 'declaration.json'
    run = batch()['run']
    declaration.write_text(json.dumps({k: v for k, v in run.items()
        if k not in {'adapter_version', 'response_hash', 'frequency', 'field_basis'}}))
    destination = tmp_path / 'new.db'
    monkeypatch.setattr('sys.argv', ['stage_price_response', '--database', str(destination),
                                   '--response', str(source), '--declaration', str(declaration)])
    monkeypatch.delenv('MOZES_PRICE_STAGING_ENABLED', raising=False)
    with pytest.raises(stage.Disabled):
        stage_price_response.main()
    assert not destination.exists()
    monkeypatch.setenv('MOZES_PRICE_STAGING_ENABLED', '1')
    stage_price_response.main()
    result = json.loads(capsys.readouterr().out)
    assert result['observations'] == 1 and result['legacy_prices_written'] is False
    conn = stage.connect(destination)
    assert conn.execute('SELECT count(*) FROM staged_price_observations').fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='prices'").fetchone()[0] == 0


@pytest.mark.parametrize('body', [b'date,close,close\n2026-01-02,100,50\n',
                                b'date,close,ticker\n2026-01-02,100,OTHER\n',
                                b'date,close\n2026-01-02,100,extra\n'])
def test_ambiguous_csv_columns_or_instrument_identity_are_not_silently_reinterpreted(body):
    with pytest.raises(ValueError):
        batch(body=body)


def test_ticker_alone_is_not_a_verified_instrument_identity():
    with pytest.raises(ValueError, match='namespaced_instrument_identity_required'):
        batch(instrument_id='TEST')


def test_source_response_symbol_mismatch_is_rejected():
    body = json.loads(yahoo_body([50, 50]))
    body['chart']['result'][0]['meta']['symbol'] = 'OTHER'
    with pytest.raises(ValueError, match='provider_instrument_mismatch'):
        batch(provider='yahoo', body=json.dumps(body).encode())


@pytest.mark.parametrize('budget', ['MAX_RUNS', 'MAX_OBSERVATIONS'])
def test_storage_count_limits_preserve_existing_observations(monkeypatch, budget):
    stage, conn = staged()
    stage.stage_imports(conn, [batch()], enabled=True)
    before = list(conn.iterdump())
    monkeypatch.setattr(stage, budget, 1)
    with pytest.raises(ValueError, match='staging_storage_budget'):
        stage.stage_imports(conn, [batch('new')], enabled=True)
    assert list(conn.iterdump()) == before
