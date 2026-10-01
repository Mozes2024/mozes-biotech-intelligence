import pytest
from mozes.entity_resolution import select_primary, security_kind
from mozes.universe import best_mapping


def sec(ticker, cik=123, name='Alpha Therapeutics'):
    return {'ticker': ticker, 'cik': cik, 'name': name, 'exchange': 'Nasdaq'}


def listing(ticker, name):
    return {'ticker': ticker, 'name': name, 'exchange': 'NASDAQ', 'source_url': 'https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt'}


def test_warrants_units_rights_never_win_sec_row_order():
    rows = [sec('AAA'), sec('AAAW'), sec('AAAU'), sec('AAAR')]
    listings = [listing('AAA', 'Alpha Common Stock'), listing('AAAW', 'Alpha Warrants'),
                listing('AAAU', 'Alpha Units'), listing('AAAR', 'Alpha Rights')]
    for values in (rows, list(reversed(rows))):
        out = select_primary(values, listings)
        assert [x['ticker'] for x in out['projection']] == ['AAA']
        assert len(out['candidates']) == 4


@pytest.mark.parametrize('ticker', ['INSW', 'AU', 'R', 'GWPH'])
def test_letter_suffix_alone_does_not_exclude_real_equity(ticker):
    assert select_primary([sec(ticker)], [listing(ticker, 'Company Common Stock')])['projection'][0]['ticker'] == ticker


def test_ads_is_preserved_as_eligible_us_security():
    out = select_primary([sec('PHAR')], [listing('PHAR', 'Pharming American Depositary Shares')])
    assert out['projection'][0]['ticker'] == 'PHAR'


def test_ambiguous_share_classes_do_not_get_arbitrary_ticker():
    out = select_primary([sec('AAA'), sec('AAB')], [listing('AAA', 'Alpha Class A Common Stock'), listing('AAB', 'Alpha Class B Common Stock')])
    assert out['projection'] == []
    assert out['ambiguous'][0]['reason'] == 'ambiguous_share_classes'


def test_two_ciks_with_same_normalized_name_are_not_merged():
    out = select_primary([sec('AAA', 123, 'Alpha Inc'), sec('BBB', 456, 'Alpha Corp')],
                         [listing('AAA', 'Alpha Common Stock'), listing('BBB', 'Alpha Common Stock')])
    assert not out['projection']
    assert any(x['reason'] == 'normalized_name_collision' for x in out['ambiguous'])


def test_missing_security_description_is_not_sufficient_for_discovery():
    assert not select_primary([sec('AAA')], [listing('AAA', 'Alpha')])['projection']


def test_direct_sponsor_conflict_and_low_confidence_are_not_promoted():
    rows = [{'sponsor_norm': 'alpha', 'sponsor': 'Alpha', 'ticker': 'AAA', 'cik': '123', 'confidence': .9},
            {'sponsor_norm': 'alpha', 'sponsor': 'Alpha', 'ticker': 'BBB', 'cik': '456', 'confidence': .9}]
    assert best_mapping('Alpha', rows) is None
    assert best_mapping('Alpha', [dict(rows[0], confidence=0)]) is None
    assert best_mapping('Alpha', [rows[0]])['ticker'] == 'AAA'


def test_conflicting_issuers_for_same_symbol_are_not_resolved_by_last_row():
    out = select_primary([sec('AAA',123,'Alpha'), sec('AAA',456,'Beta')], [listing('AAA','Alpha Common Stock')])
    assert not out['projection']
    assert {x['eligibility'] for x in out['candidates']} == {'conflicting_issuer_for_symbol'}


def test_database_projection_is_idempotent_and_discards_stale_warrant_cik(tmp_path, monkeypatch):
    from mozes import db
    from mozes.entity_resolution import update_company_map
    conn = db.connect(tmp_path / 'x.db')
    db.upsert_watch(conn, 'AAA', 'Alpha', cik='999')
    db.upsert_watch(conn, 'AAAW', 'Alpha Warrants', cik='123')
    # Listing status is supplied independently of the identity-selection unit under test.
    monkeypatch.setattr(db, 'security_lifecycle_row', lambda c,t: {'status':'ACTIVE'}, raising=False)
    rows = [sec('AAA'), sec('AAAW')]
    listings = [listing('AAA','Alpha Common Stock'), listing('AAAW','Alpha Warrants')]
    update_company_map(conn, rows, listings)
    update_company_map(conn, list(reversed(rows)), listings)
    assert conn.execute('SELECT COUNT(*) FROM v2c_security_candidates').fetchone()[0] == 2
    assert conn.execute('SELECT ticker FROM sponsor_ticker_map').fetchone()[0] == 'AAA'
    assert conn.execute("SELECT cik FROM watch_universe WHERE ticker='AAA'").fetchone()[0] == '123'
    assert conn.execute("SELECT cik FROM watch_universe WHERE ticker='AAAW'").fetchone()[0] is None
