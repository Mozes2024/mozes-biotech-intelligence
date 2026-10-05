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
