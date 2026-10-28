"""Reviewed official mirrors supplement, never erase, blocked primary sources."""
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from mozes import db
from mozes.ir_registry import load_ir_registry
from mozes.news_signals import _poll_official_mirror, poll_official_feeds
from mozes.payload_v3 import _health

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
SITE = 'https://tyrabio.investorroom.com/'
FEED = SITE + 'news-releases?pagetemplate=rss'
ENTRY = {'mirror_site': SITE, 'mirror_feed_url': FEED}


def rss(title='Tyra Reports New Phase Two Trial Results', minutes=-5, host=SITE):
    stamp = (NOW + timedelta(minutes=minutes)).strftime('%a, %d %b %Y %H:%M:%S GMT')
    return (f'<rss><channel><item><title>{title}</title><link>{host}2026-10-27-{minutes}-new-release</link>'
            f'<pubDate>{stamp}</pubDate></item></channel></rss>').encode()


def test_official_mirror_is_reviewed_in_registry_and_prefetched(monkeypatch):
    from mozes import hot_monitor
    entry = load_ir_registry()['TYRA']
    assert entry['mirror_site'] == SITE and entry['mirror_feed_url'] == FEED
    monkeypatch.setattr(hot_monitor, 'priority_tickers', lambda: ['TYRA'])
    assert FEED in hot_monitor.hot_urls()


def test_mirror_activation_suppresses_old_and_equal_timestamp_releases(tmp_path):
    conn = db.connect(tmp_path / 'activation.db')
    for minutes in (-5, 0):
        isolated = db.connect(tmp_path / f'{minutes}.db')
        result = _poll_official_mirror(isolated, 'TYRA', ENTRY, fetch=lambda _: rss(minutes=minutes), now=NOW)
        assert result['status'] == 'active' and result['signals_seen'] == 0
        assert result['baseline_suppressed'] == 1
        assert isolated.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
    _poll_official_mirror(conn, 'TYRA', ENTRY, fetch=lambda _: rss(), now=NOW)
    value = rss(minutes=1)
    result = _poll_official_mirror(conn, 'TYRA', ENTRY, fetch=lambda _: value, now=NOW+timedelta(minutes=2))
    assert result['signals_seen'] == 1
    assert _poll_official_mirror(conn, 'TYRA', ENTRY, fetch=lambda _: value, now=NOW+timedelta(minutes=3))['signals_seen'] == 0
    change = conn.execute('SELECT * FROM change_events').fetchone()
    assert change['verification_state'] == 'investigation_only'
    assert change['source_type'] == 'company_ir'
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0


def test_blocked_ir_keeps_failure_and_records_working_mirror(tmp_path):
    conn = db.connect(tmp_path / 'fallback.db')
    now = datetime.now(timezone.utc)
    def fetch(url):
        if url == FEED:
            return b'<rss><channel/></rss>'
        raise HTTPError(url, 403, 'Forbidden', {}, None)
    result = poll_official_feeds(conn, [{'ticker':'TYRA'}], fetch=fetch, now=now)
    value = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:TYRA'").fetchone()[0])
    assert value['status'] == 'fetch_error' and value['official_mirror']['status'] == 'active'
    assert result['checked'] == 1 and len(result['errors']) == 1
    health = {r['source']:r for r in _health(conn, [], None, None)['source_freshness']}['official_feed:TYRA']
    assert health['status'] == 'FAILED' and health['covered_by'] == 'official_mirror'
    assert health['coverage'] == 'partial' and health['alternate_checked_at'] == now.isoformat()
    assert health['alternates'][0]['kind'] == 'official_mirror'
    assert health['last_success_at'] is None


@pytest.mark.parametrize('payload', [b'<html><body>Access challenge</body></html>', b'broken'])
def test_html_and_invalid_xml_cannot_mark_mirror_healthy(tmp_path, payload):
    conn = db.connect(tmp_path / 'invalid.db')
    def fetch(url):
        if url == FEED:
            return payload
        raise HTTPError(url, 403, 'Forbidden', {}, None)
    poll_official_feeds(conn, [{'ticker':'TYRA'}], fetch=fetch, now=NOW)
    value = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:TYRA'").fetchone()[0])
    assert value['official_mirror']['status'] == 'fetch_error'
    health = {r['source']:r for r in _health(conn, [], None, None)['source_freshness']}['official_feed:TYRA']
    assert 'covered_by' not in health
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0


def test_unreviewed_transport_and_changed_baseline_fail_closed(tmp_path):
    conn = db.connect(tmp_path / 'identity.db')
    with pytest.raises(ValueError, match='unreviewed'):
        _poll_official_mirror(conn, 'TYRA', dict(ENTRY, mirror_feed_url='https://evil.example/rss'), fetch=lambda _: rss(), now=NOW)
    _poll_official_mirror(conn, 'TYRA', ENTRY, fetch=lambda _: rss(), now=NOW)
    with pytest.raises(ValueError, match='activation'):
        _poll_official_mirror(conn, 'TYRA', dict(ENTRY, mirror_feed_url=FEED+'&new=1'), fetch=lambda _: rss(), now=NOW)
    with pytest.raises(ValueError, match='same-host'):
        _poll_official_mirror(conn, 'TYRA', ENTRY, fetch=lambda _: rss(minutes=1, host='https://evil.example/'), now=NOW+timedelta(minutes=2))


def test_mirror_future_release_waits_until_publication_time(tmp_path):
    conn = db.connect(tmp_path / 'future.db')
    _poll_official_mirror(conn, 'TYRA', ENTRY, fetch=lambda _: rss(), now=NOW)
    payload = rss(minutes=5)
    assert _poll_official_mirror(conn, 'TYRA', ENTRY, fetch=lambda _: payload, now=NOW+timedelta(minutes=1))['signals_seen'] == 0
    assert _poll_official_mirror(conn, 'TYRA', ENTRY, fetch=lambda _: payload, now=NOW+timedelta(minutes=6))['signals_seen'] == 1


def test_stale_mirror_does_not_claim_current_coverage(tmp_path):
    from mozes.live_monitor import observe
    conn = db.connect(tmp_path / 'stale.db')
    now = datetime.now(timezone.utc)
    observe(conn, 'official_feed:TYRA', {'status':'fetch_error', 'checked_at':now.isoformat(),
        'official_mirror':{'status':'active', 'checked_at':(now-timedelta(minutes=31)).isoformat(), 'feed':FEED}})
    row = _health(conn, [], None, None)['source_freshness'][0]
    assert row['status'] == 'FAILED' and 'covered_by' not in row
    assert row['alternates'][0]['status'] == 'active'  # raw evidence survives


def test_readonly_probe_uses_disposable_state_and_preserves_primary_denial(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    import tempfile
    spec = importlib.util.spec_from_file_location('probe_ir_coverage',
        Path(__file__).resolve().parents[1] / 'scripts' / 'probe_ir_coverage.py')
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    def fetch(url):
        if url == FEED:
            return rss()
        raise HTTPError(url, 403, 'Forbidden', {}, None)
    result = probe.inspect(['TYRA'], fetch=fetch, now=NOW)
    assert result['read_only'] is True
    assert result['sources'][0]['primary_status'] == 'fetch_error'
    assert result['sources'][0]['official_mirror']['status'] == 'active'
    assert result['sources'][0]['historical_signals'] == 0
    assert list(tmp_path.iterdir()) == []


def test_regeneron_reviewed_newsroom_mirror_uses_existing_activation(monkeypatch, tmp_path):
    from mozes import hot_monitor
    entry = load_ir_registry()['REGN']
    site = 'https://newsroom.regeneron.com/'
    feed = site + 'rss/news-releases.xml?items=15'
    assert entry['mirror_site'] == site and entry['mirror_feed_url'] == feed
    monkeypatch.setattr(hot_monitor, 'priority_tickers', lambda: ['REGN'])
    assert feed in hot_monitor.hot_urls()
    now = datetime.now(timezone.utc)
    def fetch(url):
        if url == feed:
            return rss(title='Regeneron Reports New Phase Three Results', host=site)
        raise HTTPError(url, 403, 'Forbidden', {}, None)
    conn = db.connect(tmp_path / 'regn.db')
    poll_official_feeds(conn, [{'ticker':'REGN'}], fetch=fetch, now=now)
    value = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:REGN'").fetchone()[0])
    assert value['status'] == 'fetch_error'
    assert value['official_mirror']['status'] == 'active'
    assert value['official_mirror']['signals_seen'] == 0
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
