from datetime import datetime, timedelta, timezone
import pytest
from mozes import db
from mozes.news_signals import _poll_arrowhead_newsroom

NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)


def item(i, date='October 28, 2026', host=''):
    return (f'<div class="news"><p class="date">{date}</p><a href="{host}/en-us/newsroom/release-{i}">'
            f'<h2>Arrowhead Announces New Clinical Results Number {i}</h2></a></div>')


def test_baseline_and_new_release_once(tmp_path):
    conn = db.connect(tmp_path / 'arrow.db')
    old = ''.join(item(i, 'October 27, 2026') for i in range(1, 13))
    assert _poll_arrowhead_newsroom(conn, 'ARWR', fetch=lambda _: old, now=NOW)['signals_seen'] == 0
    fetch = lambda _: item(99) + old
    assert _poll_arrowhead_newsroom(conn, 'ARWR', fetch=fetch, now=NOW + timedelta(minutes=1))['signals_seen'] == 1
    assert _poll_arrowhead_newsroom(conn, 'ARWR', fetch=fetch, now=NOW + timedelta(minutes=2))['signals_seen'] == 0
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0


def test_gap_does_not_advance_baseline(tmp_path):
    conn = db.connect(tmp_path / 'gap.db')
    _poll_arrowhead_newsroom(conn, 'ARWR', fetch=lambda _: ''.join(item(i) for i in range(1, 13)), now=NOW)
    before = conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_newsroom_baseline:ARWR'").fetchone()[0]
    with pytest.raises(ValueError, match='coverage gap'):
        _poll_arrowhead_newsroom(conn, 'ARWR', fetch=lambda _: ''.join(item(i) for i in range(13, 25)), now=NOW)
    assert conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_newsroom_baseline:ARWR'").fetchone()[0] == before


@pytest.mark.parametrize('value', ['', '<html>blocked</html>', item(1, host='https://evil.example'),
    item(1, date='bad'), item(1, date='October 27, 2026') + item(2)])
def test_invalid_index_fails_closed(tmp_path, value):
    conn = db.connect(tmp_path / 'bad.db')
    with pytest.raises(ValueError):
        _poll_arrowhead_newsroom(conn, 'ARWR', fetch=lambda _: value, now=NOW)
    assert conn.execute('SELECT COUNT(*) FROM monitor_observations').fetchone()[0] == 0
