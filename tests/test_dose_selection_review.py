"""Reported early-phase dose data is reviewable, not a clinical-success claim."""
import json
from pathlib import Path
import pytest
from mozes.clinical_events import classify_clinical

CASE = json.loads((Path(__file__).parent / 'fixtures/xncr_dose_selection.json').read_text())


def test_actual_stored_xncr_excerpt_is_material_neutral_review():
    assert len(CASE['summary']) == 499  # stored RSS excerpt after whitespace trimming
    result = classify_clinical(CASE['headline'] + '\n' + CASE['summary'])
    assert result['relevant'] is True
    assert result['material'] is True
    assert result['actionable'] is False
    assert result['catalyst'] is False
    assert result['polarity'] == 'unknown'
    assert result['suppression_reason'] is None
    assert result['kind'] == 'clinical_update'


@pytest.mark.parametrize('text', [
    'Novel Bio will present Phase 1 dose selection data at a webcast.',
    'Novel Bio to present Phase 1 dose-selection results at ESMO.',
    'Novel Bio announces plans for Phase 1 dose selection data.',
    'Novel Bio reports plans to discuss Phase 1 dose selection results.',
    'Novel Bio announces a webcast on Phase 1 dose selection data.',
    'Novel Bio to participate in investor conference. Phase 1 dose selection data will be discussed.',
    'Novel Bio reports Phase 1 dose selection plans without results.',
    'Novel Bio presents Phase 1 trial design at a conference.',
    'Novel Bio announces Phase 1 first enrollment plans.',
])
def test_future_routine_and_plans_are_not_reported_dose_data(text):
    result = classify_clinical(text)
    assert not (result['material'] and not result['catalyst']), result
    assert result['actionable'] is False


def test_generic_issuer_report_is_not_ticker_specific():
    result = classify_clinical('Novel Bio Reports Phase 1 Dose-Selection Results for NBL100')
    assert result['material'] is True
    assert result['actionable'] is False
    assert result['polarity'] == 'unknown'


def test_fresh_wire_persists_neutral_candidate_without_positive_claim(tmp_path):
    from mozes import db
    from mozes.primary_feeds import poll_wire_feeds
    from mozes.alert_dispatch import build_stage1_payload
    from xml.sax.saxutils import escape
    from datetime import datetime, timezone
    conn = db.connect(tmp_path / 'candidate.db')
    conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',
                 ('xencor', 'Xencor Inc', 'XNCR', '1326732', .9, 'SEC-v2C-equity', '2026-10-25'))
    conn.commit()
    rss = ('<rss><channel><item><title>' + escape(CASE['headline']) + '</title><description>'
           + escape(CASE['summary']) + '</description><link>' + CASE['source_url']
           + '</link><pubDate>Sun, 25 Oct 2026 08:35:00 GMT</pubDate></item></channel></rss>').encode()
    # All configured feeds seeing the same release must still deduplicate it.
    poll_wire_feeds(conn, fetch=lambda _: rss, now=datetime(2026, 10, 25, 9, 7, tzinfo=timezone.utc))
    row = conn.execute('SELECT * FROM clinical_events').fetchone()
    assert row['ticker'] == 'XNCR'
    assert row['lifecycle'] == 'PUBLISHED'
    assert row['suppression_reason'] is None
    assert conn.execute('SELECT COUNT(*) FROM clinical_events').fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 1
    payload = build_stage1_payload(conn, row['change_id'])
    assert payload['priority'] == 'P2'
    assert payload['verification_state'] == 'investigation_only'
    assert payload['outcome']['actionable'] is False
    assert payload['outcome']['polarity'] == 'unknown'
    assert payload['verified_issuer_cik'] is None  # UNKNOWN lifecycle not overridden.
    from mozes.alert_feed import build_feed, browser_feed
    feed = build_feed(conn)
    assert len(feed['alerts']) == 1  # P2 neutral candidate is visible in review list.
    assert browser_feed(feed)['alerts'] == []  # Not an eligible confirmed popup.
    before = conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0]
    assert before == 1
    poll_wire_feeds(conn, fetch=lambda _: rss, now=datetime(2026, 10, 25, 9, 8, tzinfo=timezone.utc))
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == before
