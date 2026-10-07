import json
from datetime import date

from mozes import db
from mozes.alert_feed import build_feed, publish_feed
from mozes.live_monitor import record_change
from mozes.pipeline_v2c import export_payload


def test_export_writes_small_alerts_and_update_manifest(tmp_path):
    conn = db.connect(tmp_path / 'db')
    payload = export_payload(conn, tmp_path / 'web' / 'data.json')
    feed = json.loads((tmp_path / 'web' / 'alerts.json').read_text(encoding='utf-8'))
    updates = json.loads((tmp_path / 'web' / 'updates.json').read_text(encoding='utf-8'))
    assert feed['schema'] == 1 and feed['alerts'] == payload['alerts']
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
