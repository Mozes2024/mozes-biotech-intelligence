from datetime import date
import copy
import sqlite3

import pytest

from mozes import db
from mozes.financial_intelligence import (calculate_financials, store_financials, financial_context,
                                          CASH_TAG, INVESTMENT_TAGS, CFO_TAG, MONTH_DAYS)


def fact(value, end='2026-06-30', filed='2026-08-01', start=None, accn='0000000123-26-000001'):
    row = {'val': value, 'end': end, 'filed': filed, 'form': '10-Q', 'accn': accn}
    if start:
        row['start'] = start
    return row


def company(cash=120e6, flow=-30e6):
    return {'cik': 123, 'facts': {'us-gaap': {
        CASH_TAG: {'units': {'USD': [fact(cash)]}},
        CFO_TAG: {'units': {'USD': [fact(flow, start='2026-04-01')]}},
    }}}


def test_cash_only_is_lower_bound_not_missing_investments_equal_zero():
    out = calculate_financials(company(), as_of=date(2026, 8, 2), expected_cik='0000000123')
    assert out['cash_usd'] == 120e6
    assert out['investments_usd'] is None
    assert out['liquidity_basis'] == 'cash_only'
    assert out['runway_at_statement_months'] == pytest.approx(120e6 / (30e6 / 91 * MONTH_DAYS))
    assert out['estimated_remaining_months'] < out['runway_at_statement_months']
    assert len(out['provenance']) == 2


def test_overlapping_investment_tags_are_not_double_counted():
    raw = company()
    for tag in INVESTMENT_TAGS:
        raw['facts']['us-gaap'][tag] = {'units': {'USD': [fact(40e6)]}}
    out = calculate_financials(raw, as_of='2026-08-02')
    assert out['liquidity_usd'] == 160e6
    assert len(out['provenance']) == 3


def test_investment_other_period_or_accession_is_not_added():
    raw = company()
    raw['facts']['us-gaap'][INVESTMENT_TAGS[0]] = {'units': {'USD': [fact(80e6, end='2026-03-31')]}}
    assert calculate_financials(raw, as_of='2026-08-02')['investments_usd'] is None
    raw['facts']['us-gaap'][INVESTMENT_TAGS[0]]['units']['USD'] = [fact(80e6, accn='different')]
    assert calculate_financials(raw, as_of='2026-08-02')['investments_usd'] is None


def test_no_future_filing_or_same_day_fact_leaks_into_estimate():
    raw = company()
    assert calculate_financials(raw, as_of='2026-08-01')['status'] == 'missing'
    raw['facts']['us-gaap'][CASH_TAG]['units']['USD'].append(fact(900e6, filed='2026-10-01'))
    assert calculate_financials(raw, as_of='2026-09-01')['cash_usd'] == 120e6


def test_restated_fact_used_only_when_available():
    raw = company()
    raw['facts']['us-gaap'][CASH_TAG]['units']['USD'].append(fact(100e6, filed='2026-09-01', accn='0000000123-26-000002'))
    assert calculate_financials(raw, as_of='2026-08-15')['cash_usd'] == 120e6
    assert calculate_financials(raw, as_of='2026-09-02')['cash_usd'] == 100e6


def test_ytd_average_is_not_presented_as_quarterly_flow():
    raw = company(flow=-60e6)
    raw['facts']['us-gaap'][CFO_TAG]['units']['USD'][0]['start'] = '2026-01-01'
    out = calculate_financials(raw, as_of='2026-08-02')
    assert out['flow_method'] == 'reported_period_average'
    assert out['burn_per_month_usd'] == pytest.approx(60e6 / 181 * MONTH_DAYS)


def test_coherent_ytd_difference_uses_same_filing():
    raw = company()
    raw['facts']['us-gaap'][CFO_TAG]['units']['USD'] = [fact(-70e6, start='2026-01-01'), fact(-20e6, end='2026-03-31', start='2026-01-01')]
    out = calculate_financials(raw, as_of='2026-08-02')
    assert out['flow_method'] == 'quarter_from_same_filing_ytd_difference'
    assert out['operating_net_flow_usd'] == -50e6


def test_cross_filing_ytd_uses_average_not_unsupported_subtraction():
    raw = company()
    raw['facts']['us-gaap'][CFO_TAG]['units']['USD'] = [fact(-70e6, start='2026-01-01'), fact(-20e6, end='2026-03-31', start='2026-01-01', accn='other')]
    assert calculate_financials(raw, as_of='2026-08-02')['flow_method'] == 'reported_period_average'


@pytest.mark.parametrize('flow', [0, 12e6])
def test_positive_or_zero_cfo_is_not_infinite_runway(flow):
    out = calculate_financials(company(flow=flow), as_of='2026-08-02')
    assert out['estimated_remaining_months'] is None
    assert out['runway_band'] == 'not_burning_operating_cash'


def test_wrong_cik_is_rejected():
    with pytest.raises(ValueError):
        calculate_financials(company(), as_of='2026-08-02', expected_cik='456')


@pytest.mark.parametrize('value', [-10, True, '100'])
def test_invalid_cash_values_are_not_used(value):
    assert calculate_financials(company(cash=value), as_of='2026-08-02')['status'] == 'missing'


def test_eur_and_restricted_cash_are_not_substitutes():
    raw = company()
    raw['facts']['us-gaap'][CASH_TAG]['units'] = {'EUR': [fact(100e6)]}
    raw['facts']['us-gaap']['CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'] = {'units': {'USD': [fact(500e6)]}}
    assert calculate_financials(raw, as_of='2026-08-02')['status'] == 'missing'


def test_old_statements_cannot_generate_current_runway():
    out = calculate_financials(company(), as_of='2027-03-01')
    assert out['status'] == 'stale'
    assert out['estimated_remaining_months'] is None


def test_store_is_idempotent_immutable_and_reages_without_mutation(tmp_path):
    conn = db.connect(tmp_path / 'f.db')
    raw = company()
    out = store_financials(conn, 'AAA', raw, as_of='2026-08-02', expected_cik=123)
    store_financials(conn, 'AAA', raw, as_of='2026-08-02', expected_cik=123)
    assert conn.execute('SELECT COUNT(*) FROM v2c_financial_snapshots').fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE v2c_financial_snapshots SET ticker='ZZZ'")
    later = financial_context(conn, 'AAA', as_of='2026-09-02')
    assert later['estimated_remaining_months'] < out['estimated_remaining_months']
    assert financial_context(conn, 'AAA', as_of='2026-07-01')['status'] == 'missing'
    assert financial_context(conn, 'AAA', as_of='2027-03-01')['status'] == 'stale'


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
def test_non_json_numbers_reject_corrupt_source(value):
    with pytest.raises(ValueError):
        calculate_financials(company(cash=value), as_of='2026-08-02')


def test_new_financial_inputs_generate_one_explanatory_change(tmp_path):
    conn = db.connect(tmp_path / 'f.db')
    raw = company()
    store_financials(conn, 'AAA', raw, as_of='2026-08-02', expected_cik=123)
    calls = []
    def recorder(conn, **fields):
        calls.append(fields)
    raw['facts']['us-gaap'][CASH_TAG]['units']['USD'][0]['val'] = 50e6
    store_financials(conn, 'AAA', raw, as_of='2026-08-03', expected_cik=123, recorder=recorder)
    store_financials(conn, 'AAA', raw, as_of='2026-08-03', expected_cik=123, recorder=recorder)
    assert len(calls) == 1
    assert calls[0]['change_type'] == 'cash_runway_updated'
    assert calls[0]['new_value']['cash_usd'] == 50e6
    assert calls[0]['verification_state'] == 'derived'
