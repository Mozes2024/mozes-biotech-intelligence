import pytest
from mozes.financing_intelligence import analyze_financing


@pytest.mark.parametrize('form', ['S-3', 'S-3/A', 'S-3ASR', 'S-1', 'S-1/A'])
def test_registration_does_not_mean_cash_received(form):
    out = analyze_financing(form, 'We may offer up to $200 million of securities.')
    assert out['kind'] == 'registration_only'
    assert not out['cash_raised_verified']
    assert out['facts'][0]['kind'] == 'authorized_capacity'


def test_atm_capacity_is_not_proceeds():
    out = analyze_financing('424B5', 'We entered an at-the-market sales agreement to sell up to $150 million of shares.')
    assert out['kind'] == 'atm_capacity'
    assert not out['cash_raised_verified']
    assert out['facts'][0]['value'] == 150e6


def test_completed_offering_gross_and_net_are_distinct():
    text = 'The company completed its public offering with gross proceeds of $50 million and net proceeds of $47 million.'
    out = analyze_financing('8-K', text)
    assert out['kind'] == 'completed_offering'
    assert out['cash_raised_verified']
    assert [(f['kind'], f['value']) for f in out['facts']] == [('gross_proceeds', 50e6), ('net_proceeds', 47e6)]
    for f in out['facts']:
        assert text[f['span']['start']:f['span']['end']] == f['span']['text']
        assert text[f['context_start']:f['context_end']] == f['context']


def test_priced_is_not_completed_and_per_share_is_not_total_proceeds():
    text = 'The company announced the pricing of its public offering of 5,000,000 shares of common stock at $10 per share, for gross proceeds of $50 million.'
    out = analyze_financing('424B5', text)
    assert out['kind'] == 'priced_offering'
    assert not out['cash_raised_verified']
    assert any(f['kind'] == 'offered_common_shares' and f['value'] == 5e6 for f in out['facts'])
    assert any(f['kind'] == 'offering_price_per_share' and f['value'] == 10 for f in out['facts'])
    assert not any(f['kind'] == 'gross_proceeds' and f['value'] == 10 for f in out['facts'])


def test_resale_without_company_proceeds_is_not_primary_financing():
    out = analyze_financing('S-3', 'The selling stockholders may sell shares. We will not receive any proceeds from their sales.')
    assert out['kind'] == 'resale_registration'
    assert not out['unquantified_dilution']


def test_unrelated_balance_sheet_cash_is_not_offering_amount():
    out = analyze_financing('S-3', 'Cash was $90 million. We may offer up to $25 million in securities.')
    assert [f['value'] for f in out['facts']] == [25e6]


def test_multiple_proceeds_numbers_not_silently_collapsed():
    out = analyze_financing('8-K', 'We completed the offering. Gross proceeds were $20 million. Gross proceeds for the earlier offering were $15 million.')
    assert len(out['facts']) == 2
    assert all(f['ambiguous'] for f in out['facts'])
    assert not out['cash_raised_verified']


def test_warrant_exercise_price_not_common_share_offering_price():
    out = analyze_financing('424B5', 'We offer warrants with an exercise price of $5 per share.')
    assert out['has_warrants']
    assert not any(f['kind'] == 'offering_price_per_share' for f in out['facts'])


def test_clinical_press_release_is_not_financing():
    assert analyze_financing('8-K', 'The trial met its primary endpoint.')['kind'] == 'not_identified'


def test_foreign_dollar_amount_not_presented_as_usd():
    out = analyze_financing('8-K', 'We completed the offering with gross proceeds of C$50 million in Canadian dollars.')
    assert not out['facts']
    assert not out['cash_raised_verified']


def test_conditional_completion_not_verified_cash():
    out = analyze_financing('8-K', 'If we completed the offering, gross proceeds would be $50 million.')
    assert not out['cash_raised_verified']


def test_multiple_transactions_require_review():
    out = analyze_financing('8-K', 'We completed the offering in June. We announced a proposed public offering in September.')
    assert out['kind'] == 'multiple_transactions_review'


def test_missing_one_issuer_does_not_block_other_issuer_change_baseline(tmp_path, monkeypatch):
    from datetime import date
    from mozes import db
    from mozes.ingest import edgar
    from mozes.financing_intelligence import refresh_financing
    from mozes.intelligence_store import state_get
    conn = db.connect(tmp_path / 'x.db')
    db.upsert_watch(conn, 'AAA', cik='123')
    db.upsert_watch(conn, 'BBB')
    accession = ['one']
    monkeypatch.setattr(edgar, 'recent_filings_v2', lambda *a, **kw: [{'form':'S-3', 'accession':accession[0], 'filed':'2026-09-30', 'url':'https://www.sec.gov/Archives/edgar/data/123/' + accession[0]}])
    monkeypatch.setattr(edgar, '_get', lambda url: 'The shelf registration permits us to offer securities up to $50 million.')
    changes = []
    recorder = lambda conn, **kw: changes.append(kw)
    first = refresh_financing(conn, today=date(2026,10,1), recorder=recorder)
    assert first['status'] == 'PARTIAL' and first['missing_cik'] == ['BBB']
    assert state_get(conn, 'financing_baselined:AAA') is True
    assert changes == []
    accession[0] = 'two'
    refresh_financing(conn, today=date(2026,10,1), recorder=recorder)
    assert len(changes) == 1 and changes[0]['change_type'] == 'financing_interpreted'
    refresh_financing(conn, today=date(2026,10,1), recorder=recorder)
    assert len(changes) == 1
