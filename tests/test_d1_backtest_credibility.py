"""Research returns must not silently become executable or prospective evidence."""
from datetime import date, timedelta

import pytest

from mozes import backtest, backtest_v2, db
from mozes.market import event_return
from mozes.testing import make_price_event


def bars(n=40):
    return [{'date': (date(2025, 1, 1) + timedelta(days=i)).isoformat(),
             'close': 100 + i} for i in range(n)]


def test_t_minus_one_is_last_available_observation_not_penultimate():
    rows = bars()
    result = backtest_v2.runup_one(rows, rows, 30, 1)
    assert result['from'] == rows[-30]['date']
    assert result['to'] == rows[-1]['date']
    assert result['return'] == pytest.approx(rows[-1]['close'] / rows[-30]['close'] - 1)
    assert backtest_v2.runup_one(rows[-30:], [], 30, 1) is not None


def test_legacy_excess_never_uses_different_benchmark_dates():
    rows = bars()
    bench = [{**row, 'date': '2024' + row['date'][4:]} for row in rows]
    result = backtest.run_up_return({'prices': rows, 'bench': bench, 'as_of': '2025-02-10'}, 30, 1)
    assert result['excess'] is None


@pytest.mark.parametrize('close', [0, -1, float('nan'), float('inf'), 'bad'])
def test_invalid_attached_prices_are_unavailable(close):
    rows = bars(); rows[-1]['close'] = close
    assert backtest_v2.runup_one(rows, [], 30, 1) is None


def test_afterhours_cannot_use_a_prior_session_when_announcement_day_bar_is_missing():
    result = event_return([{'date': '2025-07-02', 'close': 10},
                           {'date': '2025-07-07', 'close': 15}], '2025-07-03', 'afterhours')
    assert result['available'] is False


def test_walk_forward_purges_training_not_available_at_test_entry():
    train = [make_price_event(f'T{i}', f'2020-12-{20+i:02d}', .01) for i in range(5)]
    test = make_price_event('X', '2021-01-15', -.01)
    fold = next(f for f in backtest.walk_forward(train + [test]) if f['year'] == '2021')
    assert fold['skipped'] is True


def test_report_explicitly_refuses_executable_results_without_provenance(tmp_path):
    conn = db.connect(tmp_path / 'synthetic.db')
    result = backtest_v2.report(conn)
    assert result['performance_kind'] == 'descriptive_event_study'
    assert result['executable_strategy']['supported'] is False
    assert result['executable_strategy']['validated'] is False
    assert result['executable_strategy']['n'] == 0
    conn.close()


@pytest.mark.parametrize('as_of,published,retrieved', [
    ('2025-06-20T00:00:00Z', '2025-06-01T00:00:00Z', '2025-06-01T01:00:00Z'),
    ('2025-05-30T00:00:00Z', '2025-06-02T00:00:00Z', '2025-06-02T01:00:00Z'),
    ('2025-05-30T00:00:00Z', '2025-05-01T00:00:00Z', '2026-01-01T00:00:00Z'),
    ('2025-05-30T00:00:00Z', None, '2025-05-30T01:00:00Z'),
    ('2025-06-01T12:00:00Z', '2025-06-01T08:00:00Z', '2025-06-01T09:00:00Z'),
    ('2025-06-01T22:00:00Z', '2025-06-01T21:00:00Z', '2025-06-01T21:30:00Z'),
])
def test_entry_cannot_borrow_later_snapshots_or_revised_sources(tmp_path, as_of, published, retrieved):
    from mozes.backtest_research import entry_audit
    conn = db.connect(tmp_path / 'x.db')
    db.archive_source(conn, 'S', 'https://example.test/s', 'company_ir', published, retrieved, 'original evidence')
    db.upsert_historical_case(conn, 'C', 'AAA', 'P3_TOPLINE', '2025-06-30T12:00:00Z', provenance=['S'])
    db.store_feature_snapshot(conn, 'F', 'C', as_of, {'expected_event_date': '2025-06-30'}, provenance=['S'], blinded=True)
    frozen = list(conn.iterdump())
    audit = entry_audit(conn, db.historical_case_rows(conn)[0], '2025-06-01')
    assert not audit['entry_snapshot_available']
    assert not audit['eligible']
    assert list(conn.iterdump()) == frozen
    conn.close()


def test_changed_catalyst_date_uses_only_pre_entry_forecast(tmp_path):
    from mozes.backtest_research import entry_audit
    conn = db.connect(tmp_path / 'x.db')
    db.archive_source(conn, 'S', 'https://example.test/s', 'company_ir', '2025-04-01T00:00:00Z', '2025-04-02T00:00:00Z', 'prior forecast')
    db.upsert_historical_case(conn, 'C', 'AAA', 'P3_TOPLINE', '2025-06-30T12:00:00Z', provenance=['S'])
    for key, stamp, expected in [('old', '2025-05-01T00:00:00Z', '2025-07-31'), ('new', '2025-06-20T00:00:00Z', '2025-06-30')]:
        db.store_feature_snapshot(conn, key, 'C', stamp, {'expected_event_date': expected}, provenance=['S'], blinded=True)
    audit = entry_audit(conn, db.historical_case_rows(conn)[0], '2025-06-01')
    assert audit['entry_snapshot_available']
    assert audit['known_schedule_date'] == '2025-07-31'
    assert 'final_date_differs_from_entry_forecast' in audit['reasons']
    assert 'provider_adjustment_provenance_unverified_META_M8' in audit['reasons']
    assert not audit['eligible']
    conn.close()


def test_cost_sensitivity_is_explicit_assumptions_not_historical_execution():
    from mozes.backtest_research import cost_sensitivity
    scenarios = cost_sensitivity(.1)
    assert scenarios[0]['net_return'] == pytest.approx(.1)
    assert all(s['assumptions_only'] for s in scenarios)
    assert scenarios[0]['net_return'] > scenarios[1]['net_return'] > scenarios[2]['net_return']
    assert scenarios[1]['net_return'] == pytest.approx(1.1 * (1-.0025)/(1+.0025)-1)
    for bad in (-1, float('nan'), 10000):
        with pytest.raises(ValueError):
            cost_sensitivity(.1, [{'name': 'bad', 'commission_bps': bad, 'half_spread_bps': 0, 'slippage_bps': 0}])


def test_splits_and_same_day_benchmark_absence_are_not_strategy_evidence(tmp_path):
    from mozes.backtest_research import entry_audit
    conn = db.connect(tmp_path / 'x.db')
    case = {'case_id': 'no-provenance', 'event_at': '2025-06-30T12:00:00Z'}
    assert not entry_audit(conn, case, '2025-06-01')['eligible']
    rows = bars(); rows[-1]['close'] /= 2
    result = backtest_v2.runup_one(rows, rows[:-1], 30, 1)
    assert result['excess'] is None
    assert result['performance_kind'] == 'descriptive_event_study'
    assert not result['executable']
    conn.close()


def test_missing_and_duplicate_sessions_fail_closed_without_inventing_calendar():
    rows = bars()
    assert backtest_v2.runup_one(rows + [rows[-1]], [], 30, 1) is None
    rows.pop(10)
    result = backtest_v2.runup_one(rows, rows, 30, 1)
    assert result['session_basis'] == 'observed_bars_calendar_unverified'
    assert not result['executable']


def test_extreme_finite_prices_do_not_create_infinite_returns():
    rows = bars(); rows[-30]['close'] = 1e-300; rows[-1]['close'] = 1e300
    assert backtest_v2.runup_one(rows, [], 30, 1) is None


def test_same_day_holiday_missing_close_and_overnight_gap_are_not_fills():
    # July 4 is a market holiday; no calendar may be invented from sparse bars.
    rows = [{'date': '2025-07-03', 'close': 10}, {'date': '2025-07-07', 'close': 20}]
    assert not event_return(rows, '2025-07-04', 'premarket')['available']
    assert not event_return(rows, '2025-07-04', 'afterhours')['available']
    result = event_return(rows, '2025-07-03', 'afterhours')
    assert result['return'] == 1
    assert result['executable'] is False


def test_test_returns_cannot_select_parameters_and_overlaps_are_not_portfolio():
    train = [make_price_event(f'T{i}', f'2020-0{i+1}-15', .01) for i in range(5)]
    down = make_price_event('X', '2021-05-15', -.01)
    up = make_price_event('X', '2021-05-15', .1)
    a = next(f for f in backtest.walk_forward(train + [down]) if f['year'] == '2021')
    b = next(f for f in backtest.walk_forward(train + [up]) if f['year'] == '2021')
    assert a['chosen'] == b['chosen']
    assert a['oos_n'] == b['oos_n'] == 0
    assert a['test']['ret']['n'] == 1
    assert backtest.run_up_backtest(train, 30, 1)['max_drawdown'] is None


def test_research_report_keeps_frozen_data_and_gates_and_labels_gross_net(tmp_path):
    from pathlib import Path
    from mozes.historical import backfill_prices, import_bundle
    from mozes.radar import bootstrap_database
    root = Path(__file__).resolve().parents[1] / 'mozes/data'
    conn = db.connect(tmp_path / 'offline.db')
    bootstrap_database(conn)
    import_bundle(conn, root / 'historical_starter.json')
    backfill_prices(conn, 'PIT-KOD-DAYBREAK-20260928', 'csv',
                    stock_file=str(root / 'starter_prices/KOD.csv'),
                    benchmark_file=str(root / 'starter_prices/XBI.csv'))
    before = list(conn.iterdump())
    result = backtest_v2.report(conn)
    assert list(conn.iterdump()) == before
    assert result['coverage']['runup_ready_cases'] == 1
    assert result['executable_strategy']['n'] == 0
    grid = next(g for g in result['runup_all_grids'] if g['entry'] == 30 and g['exit'] == 1)
    assert grid['return'] == grid['gross_return']
    assert grid['gross_return']['small_sample'] is True
    assert len(grid['net_assumed']) == 3
    assert 'net_return' not in grid['net_assumed'][0]['assumptions']
    assert grid['net_assumed'][0]['stats']['mean'] == pytest.approx(grid['gross_return']['mean'])
    assert not db.validation_row(conn, 'runup')['enabled']
    assert not db.validation_row(conn, 'hold_through')['enabled']
    conn.close()
