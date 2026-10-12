from copy import deepcopy
import hashlib
import json

import pytest

from mozes import db, financial_intelligence, financing_intelligence
from mozes.engine_v2 import score_event
from mozes.financial_availability import financial_availability
from mozes.financial_intelligence import CASH_TAG, CFO_TAG, financial_context, store_financials
from mozes.financing_intelligence import store_financing
from mozes.intelligence_store import ensure_schema
from mozes.paper import PaperBook

TODAY = '2026-10-12'
SOURCE = 'https://www.sec.gov/Archives/edgar/data/123/a/ex99.htm'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / 'finance.db'
    conn = db.connect(path)
    ensure_schema(conn)
    monkeypatch.setattr(financial_intelligence, 'utcnow', lambda: '2026-10-06T12:00:00+00:00')
    monkeypatch.setattr(financing_intelligence, 'utcnow', lambda: '2026-10-09T12:00:00+00:00')
    event = {'id': 'AAA-P3', 'ticker': 'AAA', 'type': 'P3_TOPLINE', 'dependency': 'single', 'mcap': 'small',
             'features': {'prior': 'strong', 'endpoint_consistent': True, 'population_consistent': True,
                          'design': 'rct_placebo', 'objective': True, 'n': 300},
             'verified_window': {'start': '2026-11-20', 'end': '2026-11-20', 'precision': 'exact'}}
    with conn:
        conn.execute('INSERT INTO events VALUES(?,?,?)', (event['id'], 'live', json.dumps(event)))
    db.upsert_event_state(conn, event['id'], status='SCHEDULED', verification_state='VERIFIED', verification_confidence=95)
    db.upsert_security_lifecycle(conn, 'AAA', status='ACTIVE', source_url=SOURCE)
    yield conn, event, path
    conn.close()


def facts(*, end='2026-06-30', filed='2026-08-01', cash=120e6, flow=-30e6):
    common = {'end': end, 'filed': filed, 'form': '10-Q', 'accn': '0000000123-26-000001'}
    return {'cik': 123, 'facts': {'us-gaap': {
        CASH_TAG: {'units': {'USD': [{**common, 'val': cash}]}},
        CFO_TAG: {'units': {'USD': [{**common, 'val': flow, 'start': '2026-04-01'}]}},
    }}}


def direct(conn, event, months=24, **overrides):
    db.add_event_source(conn, event['id'], 'runway-source', 'company_ir', url=SOURCE,
                        published_at='2026-10-01', retrieved_at='2026-10-02T10:00:00+00:00',
                        statement=f'Company reports cash runway for {months} months.')
    return {**event, 'runway': {'months': months, 'known_from': '2026-10-01', 'source_id': 'runway-source',
                               'heuristic_only': False, **overrides}}


def financing(conn, *, kind='completed', published='2026-10-08', accession='new'):
    text = {'completed': 'We completed the offering with gross proceeds of $50 million.',
            'registration': 'Registration of securities with aggregate offering price up to $50 million.',
            'proposed': 'We intend to offer shares in a proposed public offering.'}[kind]
    return store_financing(conn, 'AAA', {'form': 'S-3' if kind == 'registration' else '8-K',
                           'accession': accession, 'accepted': published, 'url': SOURCE}, text)


def test_sec_inputs_are_partial_context_not_verified_runway_or_an_investment(setup):
    conn, event, _ = setup
    store_financials(conn, 'AAA', facts(), as_of='2026-10-06', expected_cik=123)
    before = deepcopy(event)
    result = score_event(conn, event, TODAY)
    assert result['financial_availability']['status'] == 'SOURCE_BACKED_ESTIMATE'
    assert result['financial_availability']['source_inputs_available'] is True
    assert result['financial_availability']['heuristic_only'] is True
    assert result['evidence']['information_status'] == 'COMPLETE'
    assert result['evidence']['financial_information_status'] == 'PARTIAL'
    assert result['evidence']['missing_requirements']['clinical'] == []
    assert result['classification']['class'] == result['recommendation']['status'] == 'INSUFFICIENT_INFORMATION'
    assert result['financial_availability']['verified_runway_months'] is None
    assert event == before


def test_current_source_known_runway_is_reaged_and_shortfall_is_a_real_risk(setup):
    conn, event, _ = setup
    result = score_event(conn, direct(conn, event, months=3), TODAY)
    financial = result['financial_availability']
    assert financial['status'] == 'VERIFIED_RUNWAY'
    assert 2 < financial['verified_runway_months'] < 3
    assert financial['short_cash_runway'] and result['evidence']['financial_information_status'] == 'KNOWN'
    assert any(flag['code'] == 'short_cash_runway' for flag in result['risk_flags'])
    assert result['recommendation']['status'] == 'NOT_NOW'


def test_source_known_sufficient_runway_preserves_complete_research_and_locked_gates(setup):
    conn, event, _ = setup
    result = score_event(conn, direct(conn, event), TODAY)
    assert result['classification']['class'] == 'REVIEW'
    assert result['evidence']['missing_requirements']['financial'] == []
    assert not result['gates']['hold_through']['satisfied'] and not result['gates']['runup']['satisfied']


def test_direct_runway_citation_does_not_relabel_sec_snapshot_hash_as_issuer_proof(setup):
    conn, event, _ = setup
    sec = store_financials(conn, 'AAA', facts(), as_of='2026-10-06', expected_cik=123)
    result = financial_availability(conn, direct(conn, event), TODAY)
    assert result['source_url'] == sec['source_url'] and result['source_hash'] == sec['source_hash']
    assert result['runway_source']['source_url'] == SOURCE
    assert result['runway_source']['source_known_at'] == '2026-10-02T10:00:00+00:00'


def test_partial_financial_information_cannot_pass_even_simulated_open_trading_gates(setup, monkeypatch):
    conn, event, _ = setup
    store_financials(conn, 'AAA', facts(), as_of='2026-10-06', expected_cik=123)
    monkeypatch.setattr('mozes.radar.validation_status', lambda _: {'runup': {'satisfied': True}, 'hold_through': {'satisfied': True}})
    result = score_event(conn, event, TODAY)
    assert result['classification']['class'] == result['recommendation']['status'] == 'INSUFFICIENT_INFORMATION'


@pytest.mark.parametrize('value', [0, 3, 24, True, '24', float('nan'), float('inf'), -1])
def test_bare_runway_never_becomes_verified_current_finance_or_a_computed_negative(setup, value):
    conn, event, _ = setup
    result = score_event(conn, {**event, 'runway_months': value}, TODAY)
    assert result['financial_availability']['verified_runway_months'] is None
    assert result['classification']['class'] == 'INSUFFICIENT_INFORMATION'
    assert not any(flag['code'] == 'short_cash_runway' for flag in result['risk_flags'])


@pytest.mark.parametrize('override', [
    {'known_from': '2026-10-12'}, {'known_from': '2026-10-13'}, {'known_from': None},
    {'known_from': '2026-04-01'}, {'known_from': 'invalid'}, {'source_id': 'unknown'},
    {'heuristic_only': True}, {'heuristic_only': None}, {'months': False}, {'months': -1},
])
def test_future_stale_unproven_or_estimated_forecast_stays_unknown(setup, override):
    conn, event, _ = setup
    result = score_event(conn, direct(conn, event, **override), TODAY)
    assert result['financial_availability']['verified_runway_months'] is None
    assert result['recommendation']['status'] == 'INSUFFICIENT_INFORMATION'


@pytest.mark.parametrize('field,value', [('published_at', '2026-10-12'), ('retrieved_at', '2026-10-13T10:00:00Z'),
                                        ('source_type', 'aggregator'), ('url', None), ('statement', None),
                                        ('url', 'http://example.test/runway')])
def test_direct_runway_requires_primary_source_known_before_cutoff(setup, field, value):
    conn, event, _ = setup
    event = direct(conn, event)
    conn.execute(f'UPDATE event_sources SET {field}=? WHERE source_id=?', (value, 'runway-source'))
    conn.commit()
    assert financial_availability(conn, event, TODAY)['information_status'] == 'MISSING'


def test_backdated_snapshot_captured_in_future_cannot_mask_older_pit_valid_inputs(setup, monkeypatch):
    conn, event, _ = setup
    old = store_financials(conn, 'AAA', facts(), as_of='2026-10-06', expected_cik=123)
    monkeypatch.setattr(financial_intelligence, 'utcnow', lambda: '2026-10-13T00:00:00Z')
    store_financials(conn, 'AAA', facts(cash=900e6), as_of='2026-10-07', expected_cik=123, recorder=lambda *a, **kw: None)
    result = financial_availability(conn, event, TODAY)
    assert result['source_hash'] == old['source_hash']
    assert result['source_known_at'] == '2026-10-06T12:00:00+00:00'


def test_same_day_capture_is_excluded_without_assuming_disclosure_time(setup, monkeypatch):
    conn, event, _ = setup
    monkeypatch.setattr(financial_intelligence, 'utcnow', lambda: '2026-10-12T00:00:00Z')
    store_financials(conn, 'AAA', facts(), as_of='2026-10-06', expected_cik=123)
    assert financial_availability(conn, event, TODAY)['status'] == 'MISSING'


def test_stale_or_same_day_filing_snapshot_is_not_usable_current_evidence(setup):
    conn, event, _ = setup
    store_financials(conn, 'AAA', facts(end='2026-03-31', filed='2026-05-01'), as_of='2026-10-06', expected_cik=123)
    assert financial_availability(conn, event, TODAY)['status'] == 'STALE'
    assert not financial_availability(conn, event, TODAY)['short_cash_runway']


def test_later_completed_financing_needs_reconciliation_not_added_cash_or_a_shortfall(setup):
    conn, event, _ = setup
    event = direct(conn, event, months=3)
    store_financials(conn, 'AAA', facts(cash=5e6), as_of='2026-10-06', expected_cik=123)
    financing(conn)
    result = score_event(conn, event, TODAY)
    assert result['financial_availability']['status'] == 'RECONCILIATION_REQUIRED'
    assert result['financial_availability']['verified_runway_months'] is None
    assert result['financial_availability']['information_status'] == 'PARTIAL'
    assert not any(flag['code'] == 'short_cash_runway' for flag in result['risk_flags'])
    assert financial_context(conn, 'AAA', as_of=TODAY)['cash_usd'] == 5e6


@pytest.mark.parametrize('kind,published', [('registration', '2026-10-08'), ('proposed', '2026-10-08'),
                                           ('completed', '2026-10-12'), ('completed', '2026-10-13')])
def test_capacity_or_future_financing_does_not_adjust_verified_current_runway(setup, kind, published):
    conn, event, _ = setup
    event = direct(conn, event)
    financing(conn, kind=kind, published=published)
    assert financial_availability(conn, event, TODAY)['status'] == 'VERIFIED_RUNWAY'


def test_financing_reconciliation_scan_is_not_limited_to_recent_display_rows(setup):
    conn, event, _ = setup
    store_financials(conn, 'AAA', facts(), as_of='2026-10-06', expected_cik=123)
    financing(conn)
    for i in range(15):
        financing(conn, kind='registration', published='2026-10-09', accession=f'shelf-{i}')
    assert financial_availability(conn, event, TODAY)['status'] == 'RECONCILIATION_REQUIRED'


def test_same_day_financing_cannot_be_assumed_included_in_date_only_forecast(setup):
    conn, event, _ = setup
    event = direct(conn, event)
    financing(conn, published='2026-10-01')
    assert financial_availability(conn, event, TODAY)['status'] == 'RECONCILIATION_REQUIRED'


def test_future_captured_financing_does_not_leak_into_reconciliation(setup, monkeypatch):
    conn, event, _ = setup
    event = direct(conn, event)
    monkeypatch.setattr(financing_intelligence, 'utcnow', lambda: '2026-10-13T12:00:00Z')
    financing(conn)
    assert financial_availability(conn, event, TODAY)['status'] == 'VERIFIED_RUNWAY'


def test_snapshot_with_future_fact_provenance_cannot_claim_source_inputs_available(setup):
    conn, event, _ = setup
    store_financials(conn, 'AAA', facts(), as_of='2026-10-06', expected_cik=123)
    row = conn.execute('SELECT * FROM v2c_financial_snapshots').fetchone()
    payload = json.loads(row['payload'])
    payload['provenance'][0]['filed'] = '2026-10-13'
    # Synthetic append only: existing evidence is never updated, even in this probe.
    with conn:
        conn.execute('INSERT INTO v2c_financial_snapshots VALUES(?,?,?,?,?,?,?,?)',
                     ('bad-future', 'AAA', row['cik'], '2026-10-07', '2026-10-07T12:00:00Z',
                      row['source_hash'], row['source_url'], json.dumps(payload)))
    result = financial_availability(conn, event, TODAY)
    assert not result['source_inputs_available'] and result['verified_runway_months'] is None
    assert result['information_status'] == 'MISSING'


def test_read_only_scoring_preserves_financials_gates_and_immutable_forward_entry(setup):
    conn, event, path = setup
    store_financials(conn, 'AAA', facts(), as_of='2026-10-06', expected_cik=123)
    ledger = {'id': event['id'], 'state': {'verification_state': 'VERIFIED'}, 'classification': {'class': 'REVIEW'},
              'evidence': {'score': 80}, 'impact': {'score': 80}}
    assert PaperBook(conn).queue_candidates([ledger], now='2026-10-01T10:00:00Z') == 1
    preserved = [tuple(row) for row in conn.execute('SELECT * FROM forward_candidates')]
    changes = conn.total_changes
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    conn.execute('PRAGMA query_only=ON')
    result = score_event(conn, event, TODAY)
    assert result['classification']['class'] == 'INSUFFICIENT_INFORMATION'
    assert conn.total_changes == changes and hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert [tuple(row) for row in conn.execute('SELECT * FROM forward_candidates')] == preserved
    assert not result['gates']['runup']['satisfied']


def test_read_only_missing_financial_tables_do_not_initialize_schema(tmp_path):
    conn = db.connect(tmp_path / 'bare.db')
    conn.execute('PRAGMA query_only=ON')
    assert financial_availability(conn, {'id': 'AAA', 'ticker': 'AAA'}, TODAY)['status'] == 'MISSING'
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2c_financial_snapshots'").fetchone()
    assert conn.total_changes == 0
    conn.close()
