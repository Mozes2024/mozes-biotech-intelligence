from datetime import datetime, timedelta, timezone
import json

from mozes import db
from mozes.discovery import store_candidates, study_to_candidate
from mozes.news_signals import discover_official_feed, parse_feed, poll_news
from mozes.refresh import _select_issuer_batch


NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


def _feed(source="https://www.reuters.com"):
    return f'''<rss><channel><item>
      <title>SELLAS REGAL trial reaches 80th event, report claims</title>
      <link>https://news.google.com/articles/example</link>
      <pubDate>Sat, 03 Oct 2026 11:00:00 GMT</pubDate>
      <source url="{source}">Reuters</source>
    </item></channel></rss>'''.encode()


def _candidate(nct, ticker, phase="PHASE3"):
    return study_to_candidate({"NCTId": nct, "LeadSponsorName": "SELLAS Life Sciences",
                               "BriefTitle": "REGAL clinical study", "Phase": phase},
                              [{"sponsor_norm": "sellas life sciences", "sponsor": "SELLAS Life Sciences",
                                "ticker": ticker, "cik": "1", "confidence": .99, "source": "SEC-v2C-equity"}])


def test_news_source_and_date_are_required():
    assert len(parse_feed(_feed(), now=NOW)) == 1
    assert parse_feed(_feed("https://reuters.com.evil.test"), now=NOW) == []
    assert parse_feed(_feed(), now=datetime(2026, 10, 6, tzinfo=timezone.utc)) == []


def test_news_is_review_only_and_idempotent(tmp_path):
    conn = db.connect(tmp_path / "news.db")
    store_candidates(conn, [_candidate("NCT00000001", "SLS")])
    fetch = lambda url: _feed()
    assert poll_news(conn, fetch=fetch, now=NOW)["signals_seen"] == 1
    assert poll_news(conn, fetch=fetch, now=NOW)["signals_seen"] == 0
    rows = conn.execute("SELECT change_type,verification_state,source_type FROM change_events").fetchall()
    assert [tuple(row) for row in rows] == [("news_signal", "investigation_only", "secondary_news")]
    assert conn.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='live'").fetchone()[0] == 0


def test_official_company_rss_is_discovered_and_kept_as_review_signal(tmp_path):
    conn = db.connect(tmp_path / "official.db")
    store_candidates(conn, [_candidate("NCT00000011", "MRNA")])
    site = "https://investors.modernatx.com/"
    feed = "https://investors.modernatx.com/feed/rss2"
    html = f'<link rel="alternate" type="application/rss+xml" href="{feed}">'
    release = b'''<rss><channel><item><title>Moderna Phase 3 trial results</title>
      <link>https://investors.modernatx.com/news-releases/example</link>
      <pubDate>Sat, 03 Oct 2026 11:00:00 GMT</pubDate></item></channel></rss>'''
    def fetch(url):
        return html.encode() if url == site else release if url == feed else b"<rss><channel/></rss>"
    assert poll_news(conn, fetch=fetch, now=NOW)["official_feeds"]["signals_seen"] == 1
    later = NOW + timedelta(hours=1)
    assert poll_news(conn, fetch=fetch, now=later)["official_feeds"]["signals_seen"] == 0
    checked = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:MRNA'").fetchone()[0])
    assert checked["checked_at"] == later.isoformat()
    row = conn.execute("SELECT change_type,source_type,verification_state FROM change_events").fetchone()
    assert tuple(row) == ("company_release_signal", "company_ir", "investigation_only")
    assert conn.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0] == 0


def test_official_feed_discovery_rejects_third_party_link():
    html = b'<link rel="alternate" type="application/rss+xml" href="https://example.com/feed">'
    assert discover_official_feed("https://investors.modernatx.com/", lambda _: html) is None


def test_late_stage_gets_bounded_queue_slots(tmp_path):
    conn = db.connect(tmp_path / "queue.db")
    store_candidates(conn, [_candidate("NCT00000002", "P3")])
    rows = [{"ticker": "A", "last_attempt": None, "first_seen": "2026-01-01", "pending_material_at": None},
            {"ticker": "P3", "last_attempt": None, "first_seen": "2026-10-01", "pending_material_at": None},
            {"ticker": "B", "last_attempt": None, "first_seen": "2026-01-02", "pending_material_at": None}]
    assert _select_issuer_batch(conn, rows, limit=2) == ["P3", "A"]


def test_requested_issuers_precede_the_general_queue(tmp_path):
    conn = db.connect(tmp_path / "priority.db")
    store_candidates(conn, [_candidate("NCT00000003", "SLS")])
    rows = [{"ticker": t, "last_attempt": None, "first_seen": "2026-01-01",
             "pending_material_at": None} for t in ("A", "SLS", "Z")]
    assert _select_issuer_batch(conn, rows, limit=2) == ["SLS", "A"]


def _zentalis_map(conn):
    with conn:
        conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                     ('zentalis', 'Zentalis Pharmaceuticals, Inc.', 'ZNTL', '1725160', .99,
                      'SEC-v2C-equity', NOW.isoformat()))


def _zentalis_feed(title='Zentalis reports Phase 2 topline data expected in 2026'):
    return _feed().replace(b'SELLAS REGAL trial reaches 80th event, report claims', title.encode())


def test_dynamic_zentalis_not_in_watchlist_enrolls_review_signal(tmp_path):
    from mozes.news_signals import GLOBAL_QUERIES
    from mozes.pipeline_v2c import export_payload
    conn = db.connect(tmp_path / 'dynamic.db')
    _zentalis_map(conn)
    urls = []
    def fetch(url):
        urls.append(url)
        if 'clinicaltrials.gov' in url:
            return b'{"studies": []}'
        if 'news.google.com' in url:
            return _zentalis_feed()
        return b'<rss><channel/></rss>'
    result = poll_news(conn, limit=2, fetch=fetch, now=NOW)
    assert result['discovery']['issuers'] == ['ZNTL']
    watch = db.watch_rows(conn)[0]
    assert (watch['ticker'], watch['source']) == ('ZNTL', 'dynamic_news_discovery')
    assert sum('news.google.com' in u for u in urls) == len(GLOBAL_QUERIES) + 1
    assert any('clinicaltrials.gov' in u for u in urls)
    row = conn.execute('SELECT * FROM change_events').fetchone()
    assert (row['ticker'], row['verification_state']) == ('ZNTL', 'investigation_only')
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='live'").fetchone()[0] == 0
    payload = export_payload(conn, tmp_path / 'web' / 'data.json')
    assert any(c['ticker'] == 'ZNTL' for c in payload['changes'])
    assert json.loads((tmp_path / 'web' / 'data.json').read_text(encoding='utf-8'))['build']['change_ledger']
    urls.clear()
    assert poll_news(conn, limit=2, fetch=fetch, now=NOW)['signals_seen'] == 0
    assert not any('clinicaltrials.gov' in u for u in urls)


def test_dynamic_ambiguous_identity_and_untrusted_publisher_are_excluded(tmp_path):
    from mozes.news_signals import resolve_headline
    conn = db.connect(tmp_path / 'ambiguous.db')
    _zentalis_map(conn)
    with conn:
        conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                     ('other zentalis', 'Other Zentalis Pharmaceuticals', 'OTHER', '2', .99,
                      'SEC-v2C-equity', NOW.isoformat()))
    assert resolve_headline(conn, 'Other Zentalis Phase 3 topline data') is None
    assert poll_news(conn, fetch=lambda _: _zentalis_feed().replace(b'https://www.reuters.com',
                    b'https://reuters.com.evil.test'), now=NOW)['signals_seen'] == 0
    assert not db.watch_rows(conn)


def test_material_headline_terms():
    from mozes.news_signals import MATERIAL
    for term in ('Phase 2/3', 'Phase 3', 'topline data', 'readout', 'PDUFA',
                 'FDA decision', 'interim analysis', 'data expected'):
        assert MATERIAL.search('Zentalis ' + term)
    assert not MATERIAL.search('Zentalis quarterly investor conference')


def test_dynamic_enrollment_and_headline_scanning_are_bounded(tmp_path):
    from mozes.news_signals import discover_news_issuers
    conn = db.connect(tmp_path / 'bounded.db')
    for index in range(10):
        name = f'Issuername{index}'
        with conn:
            conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',
                         (name.lower(), name + ' Therapeutics', f'X{index}', str(index + 1), .99,
                          'SEC-v2C-equity', NOW.isoformat()))
    items = ''.join(_zentalis_feed(f'Issuername{i} Phase 3 readout').decode().split('<channel>')[1].split('</channel>')[0]
                    for i in range(10))
    payload = ('<rss><channel>' + items + '</channel></rss>').encode()
    def fetch(url):
        return b'{"studies": []}' if 'clinicaltrials.gov' in url else payload
    result, issuers = discover_news_issuers(conn, fetch=fetch, now=NOW, max_issuers=2)
    assert len(issuers) == len(db.watch_rows(conn)) == 2
    assert result['signals_seen'] == 2



def test_direct_publisher_feed_provenance_and_nonstandard_dates():
    payload = b"""<rss><channel><item><title><a>Zentalis Phase 3 data expected</a></title>
    <link>https://www.fiercebiotech.com/biotech/example</link>
    <pubDate>Oct 3, 2026 7:00am</pubDate></item></channel></rss>"""
    rows = parse_feed(payload, now=NOW, publisher_url='https://www.fiercebiotech.com/rss/xml')
    assert rows[0]['title'] == 'Zentalis Phase 3 data expected'
    assert rows[0]['published_at'] == '2026-10-03T11:00:00+00:00'
    assert parse_feed(payload.replace(b'www.fiercebiotech.com/biotech', b'evil.test/biotech'),
                      now=NOW, publisher_url='https://www.fiercebiotech.com/rss/xml') == []


def test_sector_discovery_rotates_instead_of_starving_unknown_issuers(tmp_path):
    from mozes.news_signals import discover_news_issuers
    conn = db.connect(tmp_path / 'rotation.db')
    for index in range(3):
        with conn:
            conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',
                         (f'issuername{index}', f'Issuername{index} Therapeutics', f'X{index}', str(index + 1),
                          .99, 'SEC-v2C-equity', NOW.isoformat()))
    items = ''.join(_zentalis_feed(f'Issuername{i} Phase 2 data expected').decode().split('<channel>')[1].split('</channel>')[0]
                    for i in range(3))
    payload = ('<rss><channel>' + items + '</channel></rss>').encode()
    def fetch(url):
        return b'{"studies": []}' if 'clinicaltrials.gov' in url else payload
    first, _ = discover_news_issuers(conn, fetch=fetch, now=NOW, max_issuers=1)
    second, _ = discover_news_issuers(conn, fetch=fetch, now=NOW + timedelta(minutes=15), max_issuers=1)
    assert first['issuers'] == ['X0']
    assert second['issuers'] == ['X1']


def test_primary_verification_is_scoped_to_new_issuer(tmp_path, monkeypatch):
    from mozes.news_signals import verify_discovered
    from mozes import refresh
    conn = db.connect(tmp_path / 'primary.db')
    _zentalis_map(conn)
    issuer = dict(conn.execute('SELECT * FROM sponsor_ticker_map').fetchone())
    calls = []
    monkeypatch.setenv('SEC_USER_AGENT', 'test-identifying-agent')
    monkeypatch.setattr(refresh, 'verify_candidates_from_sec', lambda _conn, **kwargs: calls.append(('clinical', kwargs)))
    monkeypatch.setattr(refresh, 'scan_watch_universe_regulatory', lambda _conn, **kwargs: calls.append(('regulatory', kwargs)))
    def fetch(url):
        return json.dumps({'studies': [{'NCTId': 'NCT01234567', 'LeadSponsorName': issuer['sponsor'], 'Phase': 'PHASE2'}]}).encode()
    import time
    result = verify_discovered(conn, [issuer], fetch=fetch, now=NOW, deadline=time.monotonic() + 30)
    assert result['checked'] == ['ZNTL']
    assert [name for name, _ in calls] == ['clinical', 'regulatory']
    assert all(kwargs['tickers'] == {'ZNTL'} for _, kwargs in calls)
    assert conn.execute('SELECT ticker FROM discovery_candidates').fetchone()[0] == 'ZNTL'
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='live'").fetchone()[0] == 0


def test_registry_outage_does_not_prevent_sec_verification(tmp_path, monkeypatch):
    from mozes.news_signals import verify_discovered
    from mozes import refresh
    import time
    conn = db.connect(tmp_path / 'outage.db')
    _zentalis_map(conn)
    issuer = dict(conn.execute('SELECT * FROM sponsor_ticker_map').fetchone())
    calls = []
    monkeypatch.setenv('SEC_USER_AGENT', 'test-identifying-agent')
    monkeypatch.setattr(refresh, 'verify_candidates_from_sec', lambda _conn, **kwargs: calls.append('clinical'))
    monkeypatch.setattr(refresh, 'scan_watch_universe_regulatory', lambda _conn, **kwargs: calls.append('regulatory'))
    def fetch(_):
        raise OSError('registry unavailable')
    result = verify_discovered(conn, [issuer], fetch=fetch, now=NOW, deadline=time.monotonic() + 30)
    assert calls == ['clinical', 'regulatory']
    assert result['errors'][0]['source'] == 'clinicaltrials'
