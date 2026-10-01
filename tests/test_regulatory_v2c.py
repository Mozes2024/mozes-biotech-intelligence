from datetime import date
import json

from mozes import db
from mozes.dates import date_info
from mozes.regulatory_lifecycle import apply_guidance, target_window


def event(conn, eid='APP-A', status='SCHEDULED'):
    e = {'id': eid, 'ticker': 'AAA', 'type': 'PDUFA_NME', 'program': 'Drug A', 'application_id': eid,
         'chronology': [{'date': '2026-06-01', 'src': 'pre', 'text': 'Original target', 'date_text': 'October 15, 2026'}]}
    with conn:
        conn.execute("INSERT INTO events VALUES(?,'live',?)", (eid, json.dumps(e)))
    db.upsert_event_state(conn, eid, status=status, verification_state='VERIFIED')
    return e


def statement(eid='APP-A', text='FDA extended the PDUFA target action date from October 15, 2026 to January 15, 2027.', filed='2026-09-15'):
    return {'application_id': eid, 'catalyst_type': 'PDUFA', 'form': '8-K', 'filed': filed,
            'source_id': 'https://www.sec.gov/Archives/edgar/data/123/abc/ex99.htm', 'statement': text}


def test_extension_updates_one_event_and_appends_version(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    event(conn)
    out = apply_guidance(conn, 'AAA', statement(), today=date(2026,10,1))
    assert out['status'] == 'updated'
    assert out['window']['start'] == '2027-01-15'
    assert db.count_events(conn) == 1
    loaded = db.load_events(conn, 'live')[0]
    assert loaded['chronology'][0]['superseded'] is True
    assert len(loaded['chronology']) == 2
    assert date_info(loaded['chronology'], {})['window']['start'] == '2027-01-15'
    assert db.event_state(conn, 'APP-A')['event_timestamp'] is None
    assert db.event_state(conn, 'APP-A')['event_session'] == 'unknown'
    again = apply_guidance(conn, 'AAA', statement(), today=date(2026,10,1))
    assert again['status'] == 'unchanged'
    assert conn.execute('SELECT COUNT(*) FROM v2c_regulatory_versions').fetchone()[0] == 1


def test_two_applications_same_date_are_not_merged(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    event(conn, 'APP-A'); event(conn, 'APP-B')
    apply_guidance(conn, 'AAA', statement('APP-A'), today=date(2026,10,1))
    b = next(e for e in db.load_events(conn) if e['id'] == 'APP-B')
    assert len(b['chronology']) == 1
    assert db.count_events(conn) == 2


def test_unknown_application_is_review_not_auto_verified(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    s = statement(); s.pop('application_id')
    out = apply_guidance(conn, 'AAA', s, today=date(2026,10,1))
    assert out['status'] == 'review'
    assert db.count_events(conn) == 0


def test_approved_application_cannot_be_reopened(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    event(conn, status='APPROVED')
    out = apply_guidance(conn, 'AAA', statement(), today=date(2026,10,1))
    assert out['reason'] == 'terminal_application_not_reopened'
    assert db.event_state(conn, 'APP-A')['status'] == 'APPROVED'


def test_older_disclosure_cannot_overwrite_new_guidance(tmp_path):
    conn = db.connect(tmp_path / 'x.db'); event(conn)
    apply_guidance(conn, 'AAA', statement(), today=date(2026,10,1))
    old = statement(text='PDUFA target action date is December 1, 2026.', filed='2026-07-01')
    assert apply_guidance(conn, 'AAA', old, today=date(2026,10,1))['reason'] == 'older_source'


def test_past_annual_report_and_non_sec_source_are_not_auto_upcoming(tmp_path):
    conn = db.connect(tmp_path / 'x.db'); event(conn)
    s = statement(); s['form'] = '10-K'
    assert apply_guidance(conn, 'AAA', s, today=date(2026,10,1))['status'] == 'review'
    s = statement(text='PDUFA date was September 1, 2026.')
    assert apply_guidance(conn, 'AAA', s, today=date(2026,10,1))['reason'] == 'past_window_not_upcoming'
    s = statement(); s['source_id'] = 'https://sec.gov.fake/Archives/edgar/data/a'
    assert apply_guidance(conn, 'AAA', s, today=date(2026,10,1))['status'] == 'review'


def test_broad_window_confidence_tracks_precision(tmp_path):
    conn = db.connect(tmp_path / 'x.db'); event(conn)
    out = apply_guidance(conn, 'AAA', statement(text='PDUFA target action date has been changed to H1 2027.'), today=date(2026,10,1))
    assert out['status'] == 'updated'
    assert db.event_state(conn, 'APP-A')['verification_confidence'] == 35


def test_narrow_then_broader_restatement_does_not_degrade_date(tmp_path):
    conn = db.connect(tmp_path / 'x.db'); event(conn)
    apply_guidance(conn, 'AAA', statement(), today=date(2026,10,1))
    broad = statement(text='PDUFA target action date is H1 2027.', filed='2026-09-16')
    assert apply_guidance(conn, 'AAA', broad, today=date(2026,10,1))['reason'] == 'broader_restatement_does_not_erase_precision'


def test_adcom_does_not_overwrite_application_action_date(tmp_path):
    conn = db.connect(tmp_path / 'x.db'); event(conn)
    s = statement(); s['catalyst_type'] = 'ADCOM'
    assert apply_guidance(conn, 'AAA', s, today=date(2026,10,1))['status'] == 'review'
    assert len(db.load_events(conn)[0]['chronology']) == 1


def test_conflicting_date_only_sources_same_day_need_review(tmp_path):
    conn = db.connect(tmp_path / 'x.db'); event(conn)
    apply_guidance(conn, 'AAA', statement(), today=date(2026,10,1))
    same_day = statement(text='PDUFA target action date is February 15, 2027.')
    assert apply_guidance(conn, 'AAA', same_day, today=date(2026,10,1))['reason'] == 'same_day_conflict_needs_publication_time'


def test_other_issuer_filing_is_not_used_for_ticker_guidance(tmp_path):
    conn = db.connect(tmp_path / 'x.db'); event(conn)
    db.upsert_watch(conn, 'AAA', 'Alpha', cik='456')
    assert apply_guidance(conn, 'AAA', statement(), today=date(2026,10,1))['reason'] == 'source_issuer_does_not_match_ticker'
    assert len(db.load_events(conn)[0]['chronology']) == 1
