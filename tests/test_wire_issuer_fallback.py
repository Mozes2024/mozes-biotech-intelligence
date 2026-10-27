"""Per-issuer GlobeNewswire keyword-feed fallback while the official IR source is blocked."""
import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from mozes import db
from mozes.news_signals import parse_wire_issuer_feed, poll_official_feeds
from mozes.payload_v3 import _health

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
WIRE_URL = "https://rss.globenewswire.com/RssFeed/keyword/BioCryst"


@pytest.mark.parametrize(('ticker', 'feed_id', 'contributor'), [
    ('CRSP', 'yZonBXkVeWlY2q7tH3QlaQ==', 'CRISPR Therapeutics AG'),
    ('SLS', 'Xh-RfjSN_ddYlwURi9qAmA==', 'SELLAS Life Sciences Group, Inc.'),
])
def test_reviewed_organization_feeds_are_registered_and_prefetched(ticker, feed_id, contributor, monkeypatch):
    from mozes import hot_monitor
    from mozes.ir_registry import load_ir_registry
    entry = load_ir_registry()[ticker]
    assert entry['wire_feed_url'] == 'https://rss.globenewswire.com/RssFeed/Organization/' + feed_id
    assert entry['wire_contributor'] == contributor
    monkeypatch.setattr(hot_monitor, 'priority_tickers', lambda: [ticker])
    assert entry['wire_feed_url'] in hot_monitor.hot_urls()


def _gn_feed(items):
    body = "".join(
        f'<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate>'
        f'<dc:contributor>{c}</dc:contributor></item>'
        for t, u, d, c in items)
    return (f'<?xml version="1.0"?><rss version="2.0"><channel '
            f'xmlns:dc="http://dublincore.org/documents/dcmi-namespace/">'
            f'<title>GlobeNewswire RSS Feed</title>{body}</channel></rss>').encode()


ITEMS = [
    ("BCRX INVESTORS ALERT: Rosen Law Firm Reminds Investors",
     "https://www.globenewswire.com/news-release/2026/10/20/1/0/en/alert.html",
     "Tue, 20 Oct 2026 10:00:00 GMT", "The Rosen Law Firm"),
    ("BioCryst Announces U.S. FDA Approval of ORLADEYO Oral Pellets",
     "https://www.globenewswire.com/news-release/2026/10/20/2/0/en/orladeyo.html",
     "Tue, 20 Oct 2026 11:00:00 GMT", "BioCryst Pharmaceuticals, Inc."),
]


def test_parse_wire_issuer_feed_keeps_only_the_issuers_own_releases():
    rows = parse_wire_issuer_feed(_gn_feed(ITEMS), contributor="biocryst", now=NOW)
    assert [r["title"] for r in rows] == ["BioCryst Announces U.S. FDA Approval of ORLADEYO Oral Pellets"]
    # third-party article hosts are not trusted even when the contributor matches
    spoofed = [("BioCryst x", "https://evil.example.com/x.html", "Tue, 20 Oct 2026 11:00:00 GMT",
                "BioCryst Pharmaceuticals, Inc.")]
    assert parse_wire_issuer_feed(_gn_feed(spoofed), contributor="biocryst", now=NOW) == []
    assert parse_wire_issuer_feed(_gn_feed(ITEMS), contributor="", now=NOW) == []


def _fetch_factory(wire_payload):
    def fetch(url):
        if url == WIRE_URL:
            return wire_payload
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    return fetch


def test_blocked_ir_falls_back_to_wire_feed_with_forward_only_baseline(tmp_path):
    conn = db.connect(tmp_path / "wire.db")
    issuer = {"ticker": "BCRX", "sponsor": "BioCryst"}
    now = datetime.now(timezone.utc)
    items = [(*item[:2], (now-timedelta(hours=1)).strftime('%a, %d %b %Y %H:%M:%S GMT'), item[3]) for item in ITEMS]
    result = poll_official_feeds(conn, [issuer], fetch=_fetch_factory(_gn_feed(items)), now=now)
    assert result["signals_seen"] == 0  # first activation is a baseline, never a replay
    obs = json.loads(conn.execute(
        "SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:BCRX'").fetchone()[0])
    assert obs["status"] == "fetch_error"  # the blocked IR page is not hidden
    assert obs["wire_fallback"]["status"] == "active"
    assert obs["wire_fallback"]["baseline_suppressed"] == 1  # the pre-baseline release
    baseline = json.loads(conn.execute(
        "SELECT value_json FROM monitor_observations WHERE observation_key='wire_issuer_baseline:globe_kw_v1:BCRX'"
    ).fetchone()[0])
    assert baseline["transport_url"] == WIRE_URL
    rows = {r["source"]: r for r in _health(conn, [], None, None)["source_freshness"]}
    assert rows["official_feed:BCRX"]["status"] == "FAILED"
    assert rows["official_feed:BCRX"]["covered_by"] == "wire_issuer"  # no SEC streams needed


def test_new_wire_release_is_recorded_once_after_baseline(tmp_path):
    conn = db.connect(tmp_path / "wire2.db")
    issuer = {"ticker": "BCRX", "sponsor": "BioCryst"}
    poll_official_feeds(conn, [issuer], fetch=_fetch_factory(_gn_feed(ITEMS)), now=NOW)
    later = NOW + timedelta(hours=2)
    fresh = ITEMS + [("BioCryst to Report Third Quarter 2026 Financial Results",
                      "https://www.globenewswire.com/news-release/2026/10/22/3/0/en/q3.html",
                      "Thu, 22 Oct 2026 13:30:00 GMT", "BioCryst Pharmaceuticals, Inc.")]
    result = poll_official_feeds(conn, [issuer], fetch=_fetch_factory(_gn_feed(fresh)), now=later)
    assert result["signals_seen"] == 1
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 1
    # the same poll again must not duplicate the release
    again = poll_official_feeds(conn, [issuer], fetch=_fetch_factory(_gn_feed(fresh)), now=later)
    assert again["signals_seen"] == 0
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 1


@pytest.mark.parametrize('minutes', [0, 5])
def test_new_wire_feed_never_replays_activation_timestamp_or_future_release(tmp_path, minutes):
    conn = db.connect(tmp_path / 'activation-edge.db')
    issuer = {'ticker':'TVTX'}
    def fetch(url):
        if 'globenewswire.com' in url:
            return _gn_feed([('Travere Reports Phase Two Trial Results',
                'https://www.globenewswire.com/news-release/2026/10/22/1/0/en/trial.html',
                (NOW+timedelta(minutes=minutes)).strftime('%a, %d %b %Y %H:%M:%S GMT'), 'Travere Therapeutics, Inc.')])
        raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)
    assert poll_official_feeds(conn, [issuer], fetch=fetch, now=NOW)['signals_seen'] == 0
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
    later = poll_official_feeds(conn, [issuer], fetch=fetch, now=NOW+timedelta(minutes=10))
    assert later['signals_seen'] == (1 if minutes else 0)


def test_valid_html_response_is_not_a_successful_wire_scan(tmp_path):
    conn = db.connect(tmp_path / 'html-challenge.db')
    def fetch(url):
        if url == WIRE_URL:
            return b'<html><body>Challenge</body></html>'
        raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)
    poll_official_feeds(conn, [{'ticker':'BCRX'}], fetch=fetch, now=NOW)
    obs = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:BCRX'").fetchone()[0])
    assert obs['wire_fallback']['status'] == 'fetch_error'


def test_wire_fallback_failure_is_visible_and_fail_closed(tmp_path):
    conn = db.connect(tmp_path / "wire3.db")
    issuer = {"ticker": "BCRX", "sponsor": "BioCryst"}

    def fetch(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    result = poll_official_feeds(conn, [issuer], fetch=fetch, now=NOW)
    assert result["signals_seen"] == 0
    obs = json.loads(conn.execute(
        "SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:BCRX'").fetchone()[0])
    assert obs["status"] == "fetch_error"
    assert obs["wire_fallback"]["status"] == "fetch_error"
    rows = {r["source"]: r for r in _health(conn, [], None, None)["source_freshness"]}
    assert "covered_by" not in rows["official_feed:BCRX"]


def test_issuer_lane_skips_release_already_alerted_by_categorical_wire_feed(tmp_path):
    from mozes.live_monitor import record_change
    from mozes.primary_feeds import canonical_url
    conn = db.connect(tmp_path / "dedup.db")
    issuer = {"ticker": "BCRX", "sponsor": "BioCryst"}
    poll_official_feeds(conn, [issuer], fetch=_fetch_factory(_gn_feed(ITEMS)), now=NOW)
    url = "https://www.globenewswire.com/news-release/2026/10/22/3/0/en/q3.html"
    # the categorical feed already recorded this release (different severity, so a different change id)
    record_change(conn, ticker="BCRX", change_type="wire_release_signal", previous_value=None,
                  new_value={"headline": "BioCryst Q3 results date"}, source_url=url + "?utm_source=rss",
                  source_type="wire", severity="high", verification_state="investigation_only",
                  identity=["wire", canonical_url(url)])
    fresh = ITEMS + [("BioCryst Q3 results date", url, "Thu, 22 Oct 2026 13:30:00 GMT", "BioCryst Pharmaceuticals, Inc.")]
    result = poll_official_feeds(conn, [issuer], fetch=_fetch_factory(_gn_feed(fresh)), now=NOW + timedelta(hours=2))
    assert result["signals_seen"] == 0
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 1


def test_hot_prefetch_includes_issuer_wire_urls(monkeypatch):
    from mozes import hot_monitor
    row = {"ticker": "BCRX", "site": "https://ir.example.com/", "feed_url": None,
           "wire_feed_url": WIRE_URL, "wire_contributor": "biocryst"}
    monkeypatch.setattr(hot_monitor, "ir_entries", lambda: [row])
    monkeypatch.setattr(hot_monitor, "priority_tickers", lambda: ["BCRX"])
    urls = hot_monitor.hot_urls()
    assert WIRE_URL in urls and "https://ir.example.com/" in urls


def test_production_registry_wire_urls_are_unique_official_and_prefetched():
    from urllib.parse import urlsplit
    from mozes.hot_monitor import hot_urls
    from mozes.ir_registry import ir_entries
    from mozes.priority import priority_tickers
    wired = [r for r in ir_entries() if r.get("wire_feed_url")]
    assert len(wired) == 24  # CRSP and SLS add reviewed organization feeds
    assert all(urlsplit(r["wire_feed_url"]).hostname == "rss.globenewswire.com" for r in wired)
    urls = set(hot_urls())
    hot = set(priority_tickers())
    assert all(r["wire_feed_url"] in urls for r in wired if r["ticker"] in hot)
