"""Per-issuer GlobeNewswire keyword-feed fallback while the official IR source is blocked."""
import json
import urllib.error
from datetime import datetime, timedelta, timezone

from mozes import db
from mozes.news_signals import parse_wire_issuer_feed, poll_official_feeds
from mozes.payload_v3 import _health

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
WIRE_URL = "https://rss.globenewswire.com/RssFeed/keyword/BioCryst"


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
    result = poll_official_feeds(conn, [issuer], fetch=_fetch_factory(_gn_feed(ITEMS)), now=NOW)
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
