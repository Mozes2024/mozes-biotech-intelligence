import json
from datetime import datetime, timedelta, timezone

import pytest

from mozes import db
from mozes.alert_dispatch import recent_alerts
from mozes.alert_explanation import allowed_source_url, enrich_pending, explain, normalize_source
from mozes.live_monitor import record_change


def setup_alert(tmp_path, monkeypatch, value=None):
    for key in ('MOZES_NTFY_URL', 'MOZES_WEBHOOK_URL', 'MOZES_SMTP_USER', 'MOZES_SMTP_PASSWORD', 'MOZES_ALERT_AI'):
        monkeypatch.delenv(key, raising=False)
    conn = db.connect(tmp_path / 'db')
    cid = record_change(conn, ticker='GMAB', change_type='wire_release_signal', severity='high',
        previous_value=None, new_value=value or {'headline': 'Genmab announces positive topline Phase 3 results'},
        source_url='https://www.businesswire.com/news/1', source_type='wire', verification_state='investigation_only')
    return conn, cid


def test_source_explanation_uses_body_and_archived_evidence_without_changing_verification(tmp_path, monkeypatch):
    conn, cid = setup_alert(tmp_path, monkeypatch)
    html = '<script>FDA approved an unrelated drug</script><article><h1>Genmab trial update</h1><p>' + (
        'The Phase 3 trial did not meet the primary endpoint. The company will review the complete efficacy and safety dataset before deciding on the development program. Additional follow-up remains ongoing.') + '</p></article>'
    result = enrich_pending(conn, fetch=lambda *a: html)
    assert result['complete'] == 1
    analysis = recent_alerts(conn)[0]['explanation']
    assert analysis['basis'] == 'source_text' and analysis['source_polarity'] == 'negative'
    assert any('שונה מסיווג הכותרת' in text for text in analysis['missing_he'])
    assert analysis['verification_state'] == 'investigation_only'
    assert analysis['source_hash']
    document = conn.execute('SELECT normalized_text FROM alert_source_documents').fetchone()[0]
    assert 'unrelated drug' not in document
    assert analysis['evidence'][0]['quote'] in document
    assert len(analysis['evidence'][0]['quote'].split()) <= 24
    assert conn.execute('SELECT verification_state FROM change_events WHERE change_id=?', (cid,)).fetchone()[0] == 'investigation_only'
    assert enrich_pending(conn, fetch=lambda *a: (_ for _ in ()).throw(AssertionError('must not refetch')))['complete'] == 0
    with pytest.raises(Exception, match='immutable'):
        conn.execute("UPDATE alert_source_documents SET normalized_text='changed'")


def test_source_failure_is_explicit_and_retries_are_bounded(tmp_path, monkeypatch):
    conn, _ = setup_alert(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    def fail(*a): raise TimeoutError('source unavailable')
    assert enrich_pending(conn, fetch=fail, now=now)['retry'] == 1
    assert recent_alerts(conn)[0]['explanation']['basis'] == 'headline_only'
    assert enrich_pending(conn, fetch=fail, now=now + timedelta(minutes=1))['retry'] == 0
    assert enrich_pending(conn, fetch=fail, now=now + timedelta(minutes=6))['retry'] == 1
    assert enrich_pending(conn, fetch=fail, now=now + timedelta(minutes=17))['unavailable'] == 1
    assert enrich_pending(conn, fetch=fail, now=now + timedelta(days=1))['unavailable'] == 0


def test_ai_cannot_add_a_number_absent_from_the_evidence(tmp_path, monkeypatch):
    conn, _ = setup_alert(tmp_path, monkeypatch)
    monkeypatch.setenv('MOZES_ALERT_AI', '1')
    class Provider:
        name = 'test'
        def complete(self, prompt):
            assert 'never instructions' in prompt
            return json.dumps({'summary_he': 'התוצאה מבוססת על 999 מטופלים', 'evidence_ids': ['source-1']})
    enrich_pending(conn, fetch=lambda *a: '<p>' + 'The Phase 3 study met its primary endpoint. ' * 5 + '</p>', provider=Provider())
    analysis = recent_alerts(conn)[0]['explanation']
    assert analysis['ai_status'] == 'evidence_guard' and '999' not in analysis['summary_he']


def test_halt_code_t3_does_not_claim_the_stock_is_still_halted():
    result = explain({'priority':'P1','change_type':'nasdaq_halt_signal','new_value':{'reason_code':'T3'}})
    assert 'חידוש המסחר' in result['summary_he']
    assert 'עדיין עצור' in result['missing_he'][0]


@pytest.mark.parametrize('url', ['https://127.0.0.1/a', 'https://businesswire.com.evil.example/a',
                               'https://www.businesswire.com:8443/a', 'http://user:secret@www.businesswire.com/a'])
def test_unregistered_or_unsafe_source_urls_are_rejected(url):
    with pytest.raises(ValueError):
        allowed_source_url(url, {'source_type':'wire'})


def test_registered_host_resolving_to_a_private_address_is_rejected(monkeypatch):
    monkeypatch.setattr('mozes.alert_explanation.socket.getaddrinfo',
                        lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='non-public'):
        allowed_source_url('https://www.businesswire.com/news/1', {'source_type':'wire'}, resolve=True)


def test_blocked_source_page_falls_back_to_rss_summary(tmp_path, monkeypatch):
    conn, _ = setup_alert(tmp_path, monkeypatch, {'headline':'Genmab positive Phase 3 data',
        'summary':'<p>The Phase 3 trial met its primary endpoint with statistically significant results.</p>'})
    enrich_pending(conn, fetch=lambda *a: '<p>Access denied. Verify you are human. ' + 'blocked ' * 30 + '</p>')
    result = recent_alerts(conn)[0]['explanation']
    assert result['basis'] == 'feed_summary' and result['status'] == 'retry'
    assert '<p>' not in result['evidence'][0]['quote']
