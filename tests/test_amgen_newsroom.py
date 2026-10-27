import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from mozes import db
from mozes.news_signals import _poll_amgen_newsroom, poll_official_feeds
from mozes.payload_v3 import _health

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def payload(*rows):
    return json.dumps([{'Month': 'October 2026', 'NewsReleases': [
        {'Title': title, 'Url': url, 'Date': stamp} for title, url, stamp in rows]}]).encode()


OLD = ('Old release', '/newsroom/press-releases/2026/10/old', '10.26.2026')
NEW = ('New release', '/newsroom/press-releases/2026/10/new', '10.27.2026')


def test_baseline_suppresses_history_and_detects_new_once(tmp_path):
    conn = db.connect(tmp_path / 'newsroom.db')
    first = _poll_amgen_newsroom(conn, 'AMGN', fetch=lambda _: payload(OLD), now=NOW)
    assert first['status'] == 'active' and first['signals_seen'] == 0
    fetch = lambda _: payload(OLD, NEW)
    assert _poll_amgen_newsroom(conn, 'AMGN', fetch=fetch, now=NOW + timedelta(minutes=1))['signals_seen'] == 1
    assert _poll_amgen_newsroom(conn, 'AMGN', fetch=fetch, now=NOW + timedelta(minutes=2))['signals_seen'] == 0
    row = conn.execute('SELECT * FROM change_events').fetchone()
    assert row['verification_state'] == 'investigation_only'
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0


def test_late_historical_rows_are_not_reported_and_overflow_remains_pending(tmp_path):
    conn = db.connect(tmp_path / 'pending.db')
    _poll_amgen_newsroom(conn, 'AMGN', fetch=lambda _: payload(OLD), now=NOW)
    historical = ('Late historical release', '/newsroom/press-releases/2026/10/history', '10.25.2026')
    releases = [('Release ' + str(i), '/newsroom/press-releases/2026/10/' + str(i), '10.27.2026')
                for i in range(12)]
    fetch = lambda _: payload(OLD, historical, *releases)
    assert _poll_amgen_newsroom(conn, 'AMGN', fetch=fetch, now=NOW + timedelta(minutes=1))['signals_seen'] == 10
    assert _poll_amgen_newsroom(conn, 'AMGN', fetch=fetch, now=NOW + timedelta(minutes=2))['signals_seen'] == 2
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 12


@pytest.mark.parametrize('value', [b'<html>challenge</html>', b'[]', b'{}',
    payload(('bad', 'https://evil.example/newsroom/press-releases/bad', '10.27.2026')),
    payload(('bad', '/newsroom/press-releases/bad', 'bad-date'))])
def test_invalid_newsroom_never_creates_baseline(tmp_path, value):
    conn = db.connect(tmp_path / 'invalid.db')
    with pytest.raises(ValueError):
        _poll_amgen_newsroom(conn, 'AMGN', fetch=lambda _: value, now=NOW)
    assert conn.execute('SELECT COUNT(*) FROM monitor_observations').fetchone()[0] == 0


def test_primary_failure_remains_visible_and_mirror_adds_partial_coverage(tmp_path):
    conn = db.connect(tmp_path / 'coverage.db')
    now = datetime.now(timezone.utc)
    item = ('Release', '/newsroom/press-releases/test', now.strftime('%m.%d.%Y'))
    def fetch(url):
        if url.startswith('https://www.amgen.com/MyApi/'):
            return payload(item)
        raise HTTPError(url, 403, 'Forbidden', {}, None)
    poll_official_feeds(conn, [{'ticker': 'AMGN'}], fetch=fetch, now=now)
    value = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:AMGN'").fetchone()[0])
    assert value['status'] == 'fetch_error' and value['official_mirror']['status'] == 'active'
    health = {row['source']: row for row in _health(conn, [], None, None)['source_freshness']}['official_feed:AMGN']
    assert health['status'] == 'FAILED' and health['covered_by'] == 'official_mirror'
