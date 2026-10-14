"""A historical market cutoff cannot make today's mutable state historical."""
from datetime import date, timedelta
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from mozes import app_server, db
from mozes.intelligence_store import ensure_schema


TODAY = '2026-10-14'


@pytest.fixture
def conn(tmp_path, monkeypatch):
    class Clock(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 14)
    monkeypatch.setattr(app_server, 'date', Clock)
    conn = db.connect(tmp_path / 'x.db')
    ensure_schema(conn)
    event = {'id': 'PIT-APP', 'ticker': 'PIT', 'type': 'PDUFA_NME', 'program': 'Drug',
             'chronology': [{'date': '2026-10-13', 'src': 'later', 'date_text': 'January 15, 2027'}],
             'features': {'prior': 'strong'}, 'features_as_of': '2026-10-13'}
    with conn:
        conn.execute("INSERT INTO events VALUES(?,'live',?)", (event['id'], json.dumps(event)))
    db.upsert_event_state(conn, event['id'], status='SCHEDULED', verification_state='VERIFIED',
                          verification_confidence=95, note='Current reconciled state')
    db.store_prices(conn, 'PIT', [{'date': (date(2021,1,15) - timedelta(days=i)).isoformat(),
                                 'close': 10 + i / 10} for i in range(1, 41)], 'later-revised-data')
    yield conn
    conn.close()


def get(conn, path):
    handler = object.__new__(app_server.Handler)
    handler.path = path
    handler.server = SimpleNamespace(conn=conn)
    replies = []
    handler._send_json = lambda body, status=200: replies.append((status, body))
    handler.do_GET()
    assert len(replies) == 1
    return replies[0]


def test_historical_event_scoring_refuses_current_state_even_with_old_prices(conn):
    before = list(conn.iterdump())
    status, body = get(conn, '/api/events/PIT-APP?as_of=2021-01-15')
    assert status == 422, f'Historical request leaked current state/window: {body.get("state")}, {body.get("date")}'
    assert body['error'] == 'historical_scoring_unsupported'
    assert 'state' not in body and 'market' not in body and 'recommendation' not in body
    assert body['as_of'] == '2021-01-15' and body['supported_as_of'] == TODAY
    assert list(conn.iterdump()) == before
    db.store_prices(conn, 'PIT', [{'date': '2021-01-14', 'close': 999}], 'revision-after-cutoff')
    assert get(conn, '/api/events/PIT-APP?as_of=2021-01-15') == (status, body)


def test_historical_radar_is_not_a_historical_state_snapshot(conn):
    status, body = get(conn, '/api/radar?as_of=2021-01-15')
    assert status == 422 and body['error'] == 'historical_scoring_unsupported'
    assert 'live' not in body


@pytest.mark.parametrize('path', ['/api/events/PIT-APP', '/api/radar'])
@pytest.mark.parametrize('value', ['', 'nonsense', '2026-02-30', '20261014', '2026-W42-3',
                                  '2026-10-14T09:00:00', '2026-10-14T20:00:00Z'])
def test_invalid_or_intraday_api_asof_is_clean_400(conn, path, value):
    status, body = get(conn, path + '?' + urlencode({'as_of': value}))
    assert status == 400 and body.get('error')


@pytest.mark.parametrize('path', ['/api/events/PIT-APP', '/api/radar'])
def test_future_and_multiple_cutoffs_are_rejected_without_database_lookup(conn, path):
    assert get(None, path + '?as_of=2026-10-15')[0] == 422
    assert get(None, path + '?as_of=2026-10-14&as_of=2021-01-15')[0] == 400
    assert get(None, path + '?as_of=9999-12-31')[0] == 422


def test_valid_current_day_and_default_event_use_unchanged_scoring(conn):
    expected = app_server.score_event(conn, db.load_events(conn, 'live')[0], TODAY)
    assert get(conn, '/api/events/PIT-APP') == (200, expected)
    assert get(conn, '/api/events/PIT-APP?as_of=' + TODAY) == (200, expected)
    assert get(conn, '/api/events/unknown?as_of=' + TODAY)[0] == 404


def test_current_day_radar_remains_available(conn):
    status, body = get(conn, '/api/radar?as_of=' + TODAY)
    assert status == 200 and body['today'] == TODAY
