import json
from datetime import datetime, timedelta, timezone

import pytest

from mozes import db
from mozes.news_signals import _poll_gilead_newsroom, poll_official_feeds
from mozes.payload_v3 import _health

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)


def result(index, date='October 27, 2026', host=''):
    return {'Url': host + '/news/news-details/2026/release-' + str(index),
            'Html': '<div class="gl-notes-date field-news-date">' + date + '</div><h5>Release ' + str(index) + '</h5>'}


def payload(*rows):
    return json.dumps({'Signature': 'news', 'Count': len(rows), 'Results': rows}).encode()


def test_forward_only_baseline_and_new_release(tmp_path):
    conn = db.connect(tmp_path / 'gilead.db')
    old = result(1, 'October 26, 2026')
    first = _poll_gilead_newsroom(conn, 'GILD', fetch=lambda _: payload(old), now=NOW)
    assert first['status'] == 'active' and first['signals_seen'] == 0
    fetch = lambda _: payload(result(2), old)
    assert _poll_gilead_newsroom(conn, 'GILD', fetch=fetch, now=NOW + timedelta(minutes=1))['signals_seen'] == 1
    assert _poll_gilead_newsroom(conn, 'GILD', fetch=fetch, now=NOW + timedelta(minutes=2))['signals_seen'] == 0
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0
    assert conn.execute('SELECT verification_state FROM change_events').fetchone()[0] == 'investigation_only'


def test_missing_overlap_fails_closed_without_advancing_baseline(tmp_path):
    conn = db.connect(tmp_path / 'gap.db')
    _poll_gilead_newsroom(conn, 'GILD', fetch=lambda _: payload(result(1)), now=NOW)
    old = conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_newsroom_baseline:GILD'").fetchone()[0]
    with pytest.raises(ValueError, match='coverage gap'):
        _poll_gilead_newsroom(conn, 'GILD', fetch=lambda _: payload(*(result(i) for i in range(2, 10))), now=NOW)
    assert conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_newsroom_baseline:GILD'").fetchone()[0] == old
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0


def test_partial_primary_retained_with_official_mirror(tmp_path):
    conn = db.connect(tmp_path / 'coverage.db')
    now = datetime.now(timezone.utc)
    def fetch(url):
        if '/sxa/search/results/' in url:
            return payload(result(1, now.strftime('%B %d, %Y')))
        return b'<html><body>no RSS or press-release links</body></html>'
    poll_official_feeds(conn, [{'ticker': 'GILD'}], fetch=fetch, now=now)
    observation = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:GILD'").fetchone()[0])
    assert observation['status'] == 'no_rss_found' and observation['official_mirror']['status'] == 'active'
    health = {row['source']: row for row in _health(conn, [], None, None)['source_freshness']}['official_feed:GILD']
    assert health['status'] == 'PARTIAL' and health['covered_by'] == 'official_mirror'


@pytest.mark.parametrize('data', [b'<html>blocked</html>', b'{}', payload(),
    json.dumps({'Signature': 'news', 'Count': 20, 'Results': [result(1)]}).encode(),
    payload(result(1, host='https://evil.example')),
    payload(result(1, 'bad-date')),
    payload(result(1, 'October 26, 2026'), result(2, 'October 27, 2026'))])
def test_invalid_payload_never_activates(tmp_path, data):
    conn = db.connect(tmp_path / 'bad.db')
    with pytest.raises(ValueError):
        _poll_gilead_newsroom(conn, 'GILD', fetch=lambda _: data, now=NOW)
    assert conn.execute('SELECT COUNT(*) FROM monitor_observations').fetchone()[0] == 0
