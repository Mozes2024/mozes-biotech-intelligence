from datetime import date
import json

from mozes import db, refresh
from mozes.radar import bootstrap_database


def test_unidentified_regulatory_statement_is_review_not_blindly_verified(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / 'x.db'); bootstrap_database(conn)
    db.upsert_watch(conn, 'TEST', 'Test Bio', cik='123', source='test')
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda cik, forms=None, limit=10: [{'url':'u', 'filed':'2026-09-01', 'form':'8-K'}])
    monkeypatch.setattr(refresh, 'extract_from_filing_v2', lambda filing: [{
        'catalyst_type':'PDUFA', 'source_id':'https://www.sec.gov/Archives/edgar/data/123/abc/ex99.htm',
        'statement':'FDA assigned a PDUFA target action date of December 15, 2026.'}])
    r = refresh.scan_watch_universe_regulatory(conn, today=date(2026,10,1))
    assert r['events'] == []
    assert r['review_required'] >= 1


def test_sec_first_does_not_auto_create_clinical_readout_without_candidate(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / 'x.db'); bootstrap_database(conn)
    db.upsert_watch(conn, 'TEST', 'Test Bio', cik='123', source='test')
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda cik, forms=None, limit=10: [{'url':'u', 'filed':'2026-09-01', 'form':'8-K'}])
    monkeypatch.setattr(refresh, 'extract_from_filing_v2', lambda filing: [{
        'catalyst_type':'P3_TOPLINE', 'source_id':'https://www.sec.gov/Archives/edgar/data/123/abc/ex99.htm',
        'statement':'Phase 3 topline is expected in December 2026.'}])
    assert refresh.scan_watch_universe_regulatory(conn, today=date(2026,10,1))['events'] == []


def test_identity_bound_regulatory_guidance_is_integrated(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / 'x.db'); bootstrap_database(conn)
    db.upsert_watch(conn, 'TEST', 'Test Bio', cik='123', source='test')
    e = {'id':'TEST-APP', 'application_id':'TEST-APP', 'ticker':'TEST', 'program':'X', 'type':'PDUFA_NME', 'chronology':[]}
    with conn:
        conn.execute("INSERT INTO events VALUES(?,'live',?)", (e['id'], json.dumps(e)))
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda cik, forms=None, limit=10: [{'url':'u', 'filed':'2026-09-01', 'form':'8-K'}])
    monkeypatch.setattr(refresh, 'extract_from_filing_v2', lambda filing: [{
        'catalyst_type':'PDUFA', 'application_id':'TEST-APP',
        'source_id':'https://www.sec.gov/Archives/edgar/data/123/abc/ex99.htm',
        'statement':'FDA assigned a PDUFA target action date of December 15, 2026.'}])
    assert refresh.scan_watch_universe_regulatory(conn, today=date(2026,10,1))['events'] == ['TEST-APP']
    assert db.event_state(conn, 'TEST-APP')['status'] == 'SCHEDULED'
