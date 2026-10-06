"""Expanded hot watchlist + official IR/RSS registry."""
from datetime import datetime, timezone

from mozes.ir_registry import load_ir_registry
from mozes.priority import HOT_CAP, priority_tickers


def test_priority_watchlist_includes_genmab_and_is_expanded():
    tickers = priority_tickers()
    assert "GMAB" in tickers
    assert "MRNA" in tickers
    assert len(tickers) >= 20
    assert len(tickers) <= HOT_CAP


def test_ir_registry_has_verified_rss_for_key_names():
    registry = load_ir_registry()
    assert registry["GMAB"]["feed_url"].endswith("news-releases.xml")
    assert registry["MRNA"]["feed_url"].endswith("rss2")
    assert registry["BNTX"]["site"].startswith("https://")
    assert all(row["site"].startswith("https://") for row in registry.values())


def test_known_feed_used_without_html_discovery(tmp_path):
    from mozes import db
    from mozes.news_signals import poll_official_feeds

    conn = db.connect(tmp_path / "feeds.db")
    now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
    seen = []

    def fetch(url):
        seen.append(url)
        if url.endswith("news-releases.xml") or url.endswith("rss2"):
            return (
                f"<rss><channel><item><title>Genmab Phase 3 topline positive</title>"
                f"<link>{url}/item-1</link>"
                f"<pubDate>Mon, 06 Oct 2026 11:00:00 GMT</pubDate></item></channel></rss>"
            ).encode()
        raise AssertionError(f"unexpected fetch {url}")

    result = poll_official_feeds(conn, [{"ticker": "GMAB", "company": "Genmab"}], fetch=fetch, now=now)
    assert result["signals_seen"] == 1
    assert any(u.endswith("news-releases.xml") for u in seen)
    assert not any(u.rstrip("/").endswith("ir.genmab.com") for u in seen)
