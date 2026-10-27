"""Query-ID releases are discovery leads, with a fresh baseline on upgrade."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from mozes import db
from mozes.ir_registry import load_ir_registry
from mozes.live_monitor import observe
from mozes.news_signals import discover_press_index, poll_official_feeds

SITE = 'https://investors.alnylam.com/press-releases'
ONE = '0ea5d557-9f90-444e-9c30-3edf5dbdfe68'
TWO = '2677181f-428b-435c-b33c-cc47191b7c6f'
NOW = datetime(2026, 10, 27, 17, tzinfo=timezone.utc)
TITLE = 'Alnylam Announces New Phase Two Clinical Trial Results'


def anchor(value=ONE, *, title=TITLE, base='/press-release?id='):
    return f'<a href="{base}{value}"><span>{title}</span></a>'.encode()


def test_query_id_release_is_found_without_accepting_navigation_or_offsite_links():
    raw = anchor() + anchor(base='https://fake.example/press-release?id=')
    raw += anchor('2026', base='/press-releases?year=')
    rows = discover_press_index(SITE, lambda _:raw, entries=True)
    assert rows == [{'url':'https://investors.alnylam.com/press-release?id='+ONE, 'title':TITLE}]


@pytest.mark.parametrize('query', ['', '2026', 'not-a-uuid', ONE+'&id='+TWO, ONE+'&id='])
def test_empty_short_or_ambiguous_identifiers_cannot_become_releases(query):
    assert discover_press_index(SITE, lambda _:anchor(query), entries=True) == []


def test_query_ids_still_require_a_release_endpoint_and_descriptive_title():
    assert discover_press_index(SITE, lambda _:anchor(base='/news?id='), entries=True) == []
    assert discover_press_index(SITE, lambda _:anchor(title='Read more'), entries=True) == []
    raw = b''.join(anchor(f'{i:08x}-9f90-444e-9c30-3edf5dbdfe68') for i in range(20))
    assert len(discover_press_index(SITE, lambda _:raw, entries=True)) == 12


def test_registry_uses_verified_news_index_and_argenx_organization_feed():
    registry = load_ir_registry()
    assert registry['ALNY']['site'] == SITE
    assert registry['ARGX']['wire_feed_url'] == 'https://rss.globenewswire.com/RssFeed/Organization/2l6VaQppBelbJuj0TwBXvg=='
    assert registry['ARGX']['wire_contributor'] == 'argenx'


@pytest.mark.parametrize('legacy', [False, True])
def test_recovered_query_release_index_baselines_then_records_only_new_links(tmp_path, legacy):
    conn = db.connect(tmp_path/'query.db')
    old = 'https://investors.alnylam.com/press-release?id=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
    if legacy:
        observe(conn, 'official_html_seen:ALNY', {'urls':[old]}, source_url='https://investors.alnylam.com/', source_type='company_ir')
    page = [anchor()]
    fetch = lambda url:page[0]
    first = poll_official_feeds(conn, [{'ticker':'ALNY'}], fetch=fetch, now=NOW)
    assert first['signals_seen'] == 0
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
    value = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_html_seen:ALNY'").fetchone()[0])
    assert value['site'] == SITE and value['parser_version'] == 2
    if legacy:
        assert old in value['urls']  # old provenance is retained, not erased on upgrade
    page[0] += anchor(TWO, title='Alnylam Reports Additional New Phase Two Clinical Results')
    second = poll_official_feeds(conn, [{'ticker':'ALNY'}], fetch=fetch, now=NOW+timedelta(minutes=1))
    assert second['signals_seen'] == 1
    again = poll_official_feeds(conn, [{'ticker':'ALNY'}], fetch=fetch, now=NOW+timedelta(minutes=2))
    assert again['signals_seen'] == 0
    row = conn.execute('SELECT * FROM change_events').fetchone()
    assert row['verification_state'] == 'investigation_only'
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0


def test_changed_index_site_also_requires_a_new_forward_baseline(tmp_path, monkeypatch):
    from mozes import news_signals
    conn = db.connect(tmp_path/'changed-index.db')
    poll_official_feeds(conn, [{'ticker':'ALNY'}], fetch=lambda _:anchor(), now=NOW)
    registry = load_ir_registry()
    registry['ALNY']['site'] = SITE+'?year=2026'
    monkeypatch.setattr(news_signals, 'load_ir_registry', lambda:registry)
    result = poll_official_feeds(conn, [{'ticker':'ALNY'}], fetch=lambda _:anchor(TWO), now=NOW+timedelta(minutes=1))
    assert result['signals_seen'] == 0
