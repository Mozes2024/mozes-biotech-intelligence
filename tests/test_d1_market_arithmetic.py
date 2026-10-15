"""Dirty benchmark observations cannot become relative returns or beta/CAR."""
from datetime import date, timedelta
import json

import pytest

from mozes import db
from mozes import backtest, backtest_v2
from mozes.historical import attach_price_coverage, attached_price_rows
from mozes.market import abnormal_return, event_return, excess_return, pre_event_prices
from mozes.radar import bootstrap_database
from mozes.validation_evaluator import evaluate_walk_forward


@pytest.mark.parametrize('large,small', [(1e300, 1e-300), (1e100, 1e-100)])
def test_runup_benchmark_underflow_is_unavailable_without_losing_stock_return(large, small):
    stock = [{'date': '2025-01-01', 'close': 100}, {'date': '2025-01-02', 'close': 101}]
    benchmark = [{'date': '2025-01-01', 'close': large}, {'date': '2025-01-02', 'close': small}]
    frozen = json.dumps([stock, benchmark])
    result = backtest_v2.runup_one(stock, benchmark, 2, 1)
    legacy = backtest.run_up_return({'prices': stock, 'bench': benchmark, 'as_of': '2025-01-03'}, 2, 1)
    for row in (result, legacy):
        assert row['return'] == pytest.approx(.01)
        assert row['excess'] is None
        assert row['performance_kind'] == 'descriptive_event_study'
        assert row['executable'] is False
    assert excess_return(stock, benchmark, stock[0]['date'], stock[-1]['date']) is None
    assert json.dumps([stock, benchmark]) == frozen


def test_runup_grids_exclude_underflowed_benchmark_without_fabricated_excess():
    stock, benchmark = series(65)
    for row in benchmark[:-1]:
        row['close'] = 1e300
    benchmark[-1]['close'] = 1e-300
    grid = next(row for row in backtest_v2.all_runup_grids([{'stock': stock, 'benchmark': benchmark}])
                if row['entry'] == 30 and row['exit'] == 1)
    assert grid['gross_return']['n'] == 1
    assert grid['excess']['n'] == 0
    assert grid['missing_benchmark_windows'] == 1
    assert grid['net_assumed'][0]['stats']['n'] == 1


def test_runup_ordinary_benchmark_formula_is_identical_and_read_only():
    stock, benchmark = series(40)
    frozen = json.dumps([stock, benchmark])
    result = backtest_v2.runup_one(stock, benchmark, 30, 7)
    expected = ((stock[-7]['close'] / stock[-30]['close'] - 1)
                - (benchmark[-7]['close'] / benchmark[-30]['close'] - 1))
    assert result['excess'] == expected
    assert result['from'] == stock[-30]['date'] and result['to'] == stock[-7]['date']
    assert json.dumps([stock, benchmark]) == frozen


def test_runup_report_and_evaluator_preserve_frozen_history_and_disabled_gates(tmp_path):
    conn = db.connect(tmp_path / 'synthetic-runup.db')
    bootstrap_database(conn)
    db.archive_source(conn, 'pre', 'https://example.test/pre', 'company_ir',
                      '2024-07-01T00:00:00Z', '2024-07-02T00:00:00Z', 'pre-event evidence')
    db.archive_source(conn, 'post', 'https://example.test/post', 'company_ir',
                      '2025-01-10T13:00:00Z', '2025-01-11T00:00:00Z', 'outcome evidence')
    db.upsert_historical_case(conn, 'RUNUP', 'AAA', 'P3_TOPLINE', '2025-01-10T13:00:00Z',
                              announcement_session='premarket', provenance=['pre', 'post'])
    db.store_feature_snapshot(conn, 'F', 'RUNUP', '2025-01-09T00:00:00Z', {'fact': True},
                              provenance=['pre'], blinded=True)
    db.store_outcome_label(conn, 'L', 'RUNUP', '2025-01-11T00:00:00Z',
                           {'clinical': 'success', 'event_return': .1}, provenance=['post'], verified=True)
    stock, bench = series(220)
    pre = pre_event_prices(stock, '2025-01-10', 'premarket')
    for row in bench:
        if row['date'] == pre[-30]['date']:
            row['close'] = 1e300
        elif row['date'] == pre[-7]['date']:
            row['close'] = 1e-300
    db.store_prices(conn, 'AAA', stock, 'csv')
    db.store_prices(conn, 'XBI', bench, 'csv')
    attach_price_coverage(conn, 'RUNUP', 'csv')
    frozen = {table: list(conn.execute('SELECT * FROM ' + table)) for table in
              ('historical_cases', 'historical_case_prices', 'historical_price_attachments',
               'feature_snapshots', 'outcome_labels', 'source_archive')}
    before = list(conn.iterdump())
    report = backtest_v2.report(conn)
    assert list(conn.iterdump()) == before
    grid = next(row for row in report['runup_all_grids'] if row['entry'] == 30 and row['exit'] == 7)
    assert grid['gross_return']['n'] == 1 and grid['excess']['n'] == 0
    assert report['executable_strategy']['n'] == 0
    evaluation = evaluate_walk_forward(conn)
    assert evaluation['runup']['descriptive_n'] == evaluation['runup']['oos_n'] == 0
    assert evaluation['gates_auto_enabled'] is False
    for table, records in frozen.items():
        assert list(conn.execute('SELECT * FROM ' + table)) == records
    for name in ('runup', 'hold_through'):
        assert db.validation_row(conn, name)['enabled'] == 0
        assert db.validation_row(conn, name)['oos_n'] == 0
    conn.close()


def series(n=50):
    stock, bench = [], []
    s = b = 100.
    for i in range(n):
        r = .001 * (1 if i % 2 else -1)
        s *= 1 + 2*r; b *= 1 + r
        day = (date(2024, 8, 1) + timedelta(days=i)).isoformat()
        stock.append({'date': day, 'close': s})
        bench.append({'date': day, 'close': b})
    return stock, bench


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), 'bad'])
@pytest.mark.parametrize('index', [0, -2, -1])
def test_bad_benchmark_is_unavailable_and_stock_return_survives(value, index):
    stock, bench = series()
    bench[index]['close'] = value
    day = stock[-2]['date']
    result = abnormal_return(stock, bench, day, 'afterhours')
    assert result['available'] is False
    assert result['stock_return'] == event_return(stock, day, 'afterhours')['return']
    assert result['xbi_relative_return'] is None
    assert result['beta'] is None and result['car'] is None
    assert excess_return(stock, bench, day, stock[-1]['date']) is None


def test_duplicate_benchmark_never_silently_overwrites_a_date():
    stock, bench = series()
    bench.append({**bench[-1], 'close': 500})
    result = abnormal_return(stock, bench, stock[-2]['date'], 'afterhours')
    assert result['available'] is False and result['car'] is None
    assert excess_return(stock, bench, stock[-2]['date'], stock[-1]['date']) is None


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf'), 'bad'])
def test_excess_return_independently_rejects_bad_benchmark(value):
    stock, bench = series()
    bench[-2]['close'] = value
    assert excess_return(stock, bench, stock[-2]['date'], stock[-1]['date']) is None


def test_valid_beta_car_and_excess_formulas_are_unchanged():
    stock, bench = series()
    stock[-1]['close'] *= 1.5
    before = json.dumps([stock, bench])
    day = stock[-2]['date']
    result = abnormal_return(stock, bench, day, 'afterhours')
    stock_move = stock[-1]['close']/stock[-2]['close'] - 1
    benchmark_move = bench[-1]['close']/bench[-2]['close'] - 1
    assert result['available'] is True
    assert result['stock_return'] == pytest.approx(stock_move)
    assert result['xbi_relative_return'] == pytest.approx(stock_move-benchmark_move)
    assert result['beta'] == pytest.approx(2, abs=1e-10)
    assert result['car'] == pytest.approx(stock_move-2*benchmark_move, abs=1e-10)
    assert json.dumps([stock, bench]) == before


def test_numeric_strings_are_normalized_without_mutating_input():
    stock, bench = series()
    expected = abnormal_return(stock, bench, stock[-2]['date'], 'afterhours')
    for rows in (stock, bench):
        for row in rows:
            row['close'] = str(row['close'])
    before = json.dumps([stock, bench])
    result = abnormal_return(stock, bench, stock[-2]['date'], 'afterhours')
    assert result == expected
    assert json.dumps([stock, bench]) == before


@pytest.mark.parametrize('small,large', [(1e-300, 1e300), (1e-100, 1e100)])
def test_finite_inputs_with_overflow_do_not_publish_infinite_model_results(small, large):
    stock, bench = series()
    bench[0]['close'] = small; bench[1]['close'] = large
    result = abnormal_return(stock, bench, stock[-2]['date'], 'afterhours')
    assert result['available'] is False
    assert result['stock_return'] is not None
    assert result['beta'] is None and result['car'] is None
    json.dumps(result, allow_nan=False)
    if small == 1e-300:
        assert excess_return(stock, bench, bench[0]['date'], bench[1]['date']) is None


def test_event_window_underflow_cannot_publish_car_when_relative_return_is_unavailable():
    stock, bench = series()
    bench[-2]['close'] = 1e300; bench[-1]['close'] = 1e-300
    assert excess_return(stock, bench, stock[-2]['date'], stock[-1]['date']) is None
    result = abnormal_return(stock, bench, stock[-2]['date'], 'afterhours')
    assert result['available'] is False
    assert result['stock_return'] is not None
    assert result['beta'] is None and result['car'] is None


def test_hold_evaluator_handles_already_dirty_synthetic_frozen_benchmark(tmp_path):
    conn = db.connect(tmp_path/'synthetic.db'); bootstrap_database(conn)
    db.archive_source(conn, 'pre', 'https://example.test/pre', 'company_ir', '2024-07-01T00:00:00Z',
                      '2024-07-02T00:00:00Z', 'pre-event fact')
    db.archive_source(conn, 'post', 'https://example.test/post', 'company_ir', '2025-01-10T13:00:00Z',
                      '2025-01-11T00:00:00Z', 'outcome fact')
    db.upsert_historical_case(conn, 'HOLD', 'AAA', 'P3_TOPLINE', '2025-01-10T13:00:00Z',
                              announcement_session='premarket', provenance=['pre','post'])
    db.store_feature_snapshot(conn, 'F', 'HOLD', '2025-01-09T00:00:00Z', {'fact': True}, provenance=['pre'], blinded=True)
    db.store_outcome_label(conn, 'L', 'HOLD', '2025-01-11T00:00:00Z',
                           {'clinical':'success','event_return':.1}, provenance=['post'], verified=True)
    stock, bench = series(220)
    db.store_prices(conn, 'AAA', stock, 'csv'); db.store_prices(conn, 'XBI', bench, 'csv')
    attach_price_coverage(conn, 'HOLD', 'csv')
    # Construct an already-dirty LOCAL fixture; never rewrite a real attachment.
    with conn:
        conn.execute("UPDATE historical_case_prices SET close=0 WHERE case_id='HOLD' AND ticker='XBI' AND date=?",
                     ('2024-12-20',))
    frozen = {t:list(conn.execute('SELECT * FROM '+t)) for t in
              ('historical_case_prices','historical_price_attachments','feature_snapshots','outcome_labels','source_archive')}
    old_gates = {k:dict(db.validation_row(conn,k)) for k in ('runup','hold_through')}
    case = next(c for c in db.historical_case_rows(conn) if c['case_id'] == 'HOLD')
    s,b = attached_price_rows(conn, case)
    before = list(conn.iterdump())
    assert not abnormal_return(s,b,'2025-01-10','premarket')['available']
    assert list(conn.iterdump()) == before
    result = evaluate_walk_forward(conn)
    rows = result['hold_through']['cohorts']['P3_TOPLINE']['rows']
    assert len(rows) == 1 and rows[0]['move'] is not None
    assert rows[0]['abnormal_return']['available'] is False
    assert rows[0]['abnormal_return']['stock_return'] == rows[0]['move']
    for table, records in frozen.items():
        assert list(conn.execute('SELECT * FROM '+table)) == records
    for name, gate in old_gates.items():
        current = dict(db.validation_row(conn,name))
        assert current['enabled'] == gate['enabled'] == 0
        assert current['min_oos_n'] == gate['min_oos_n']
        assert current['oos_n'] == gate['oos_n'] == 0
    conn.close()
