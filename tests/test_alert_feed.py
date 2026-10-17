import json
from datetime import date

from mozes import db
from mozes.alert_feed import build_feed, publish_feed
from mozes.live_monitor import record_change
from mozes.pipeline_v2c import export_payload


def test_legacy_projection_and_static_fallback_keep_complete_current_list_without_mutation(tmp_path):
    from mozes.alert_feed import browser_feed
    conn = db.connect(tmp_path / 'db')
    change = record_change(conn, ticker='GMAB', change_type='company_release_signal', severity='high',
                          previous_value=None, new_value={'headline': 'Genmab positive topline'},
                          source_url='https://ir.genmab.com/news/1', source_type='company_ir')
    original = conn.execute('SELECT payload_json FROM alert_outbox WHERE change_id=?', (change,)).fetchone()[0]
    export_payload(conn, tmp_path / 'data.json')
    legacy = json.loads((tmp_path / 'alerts.json').read_text())
    current = json.loads((tmp_path / 'alerts-evidence-v1.json').read_text())
    assert len(current['alerts']) == 1 and not legacy['alerts']
    assert current['alerts'][0]['change_id'] == change
    assert conn.execute('SELECT payload_json FROM alert_outbox WHERE change_id=?', (change,)).fetchone()[0] == original
    urgent = {**current['alerts'][0], 'priority': 'P1', 'popup_eligible': True,
              'issuer_verified': True, 'confirmed_outcome': True, 'needs_review': False,
              'event_family': 'fda_decision', 'event_outcome': 'approval',
              'semantic_version': 'alert-evidence-v1', 'evidence_basis': 'source_text'}
    feed = {**current, 'alerts': [urgent, current['alerts'][0]]}
    assert browser_feed(feed)['alerts'] == [urgent]
    assert browser_feed(feed, evidence_aware=True) is feed
    assert browser_feed({**feed, 'alerts': [{**urgent, 'evidence_basis': None}]})['alerts'] == []


def test_export_writes_small_alerts_and_update_manifest(tmp_path):
    conn = db.connect(tmp_path / 'db')
    payload = export_payload(conn, tmp_path / 'web' / 'data.json')
    feed = json.loads((tmp_path / 'web' / 'alerts.json').read_text(encoding='utf-8'))
    updates = json.loads((tmp_path / 'web' / 'updates.json').read_text(encoding='utf-8'))
    full = json.loads((tmp_path / 'web' / 'alerts-evidence-v1.json').read_text(encoding='utf-8'))
    assert feed['schema'] == 1 and full['alerts'] == payload['alerts']
    assert all(row['popup_eligible'] for row in feed['alerts'])
    assert updates['revision'] == payload['build']['snapshot_revision']
    assert (tmp_path / 'web' / 'alerts.json').stat().st_size < 20_000
    assert 'health' not in feed and 'candidates' not in feed


def test_feed_revision_changes_with_channel_status_not_export_time(tmp_path, monkeypatch):
    monkeypatch.delenv('MOZES_SMTP_USER', raising=False)
    conn = db.connect(tmp_path / 'db')
    record_change(conn, ticker='GMAB', change_type='company_release_signal', severity='high',
                  previous_value=None, new_value={'headline': 'Genmab positive topline'},
                  source_url='https://ir.genmab.com/news/1', source_type='company_ir')
    before = build_feed(conn)
    assert build_feed(conn)['revision'] == before['revision']
    conn.execute("UPDATE alert_outbox SET status='sent'")
    conn.commit()
    assert build_feed(conn)['revision'] != before['revision']


def test_feed_publication_is_disabled_without_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv('MOZES_ALERT_FEED_URL', raising=False)
    assert publish_feed(db.connect(tmp_path / 'db'))['status'] == 'DISABLED'


def test_publication_uses_authenticated_https_and_does_not_publish_secret(tmp_path, monkeypatch):
    monkeypatch.setenv('MOZES_ALERT_FEED_URL', 'https://clock.example/alerts')
    monkeypatch.setenv('MOZES_ALERT_FEED_TOKEN', 'private-token')
    monkeypatch.setenv('GITHUB_RUN_NUMBER', '23')
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('MOZES_PUSH_FROM_ACTIONS', '1')
    conn = db.connect(tmp_path / 'db')
    sent = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, *a): return b'{"ok":true}'
    def opener(request, **kw):
        sent.append(request)
        return Response()
    assert publish_feed(conn, opener=opener)['status'] == 'OK'
    request = sent[0]
    assert request.get_header('Authorization') == 'Bearer private-token'
    body = json.loads(request.data)
    assert body['sequence'] == 23 and body['schema'] == 1
    assert 'private-token' not in request.data.decode()
    monkeypatch.setenv('MOZES_PUSH_FROM_ACTIONS', '0')
    assert publish_feed(conn, opener=opener)['status'] == 'DISABLED'
    assert len(sent) == 1
