"""Joint multi-issuer wire releases attribute to each verified watched issuer."""
import json
from datetime import datetime, timedelta, timezone

from mozes import db
from mozes.primary_feeds import WIRE_FEEDS, poll_wire_feeds

NOW = datetime(2026, 10, 22, 19, 5, tzinfo=timezone.utc)

JOINT_HEADLINE = ("Merck and Moderna Will Present Results From Landmark Phase 3 "
                  "INTerpath-001 Trial for Intismeran Autogene Plus KEYTRUDA "
                  "(pembrolizumab) at ESMO 2026 Presidential Symposium")
JOINT_SUMMARY = ("Merck (NYSE: MRK), known as MSD outside of the United States and Canada, "
                 "and Moderna, Inc. (Nasdaq: MRNA) today announced results from the pivotal "
                 "Phase 3 INTerpath-001 trial evaluating intismeran autogene.")


def _feed(items):
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>{s}</description>"
        f"<pubDate>{d}</pubDate></item>"
        for t, u, s, d in items)
    return ("<?xml version='1.0'?><rss version='2.0'><channel>"
            "<title>Business Wire</title>" + body + "</channel></rss>").encode()


EMPTY = _feed([])
STAMP = (NOW - timedelta(minutes=3)).strftime("%a, %d %b %Y %H:%M:%S GMT")


def _fetch(joint_items):
    payloads = {name: _feed(joint_items) if name == "businesswire_health" else EMPTY
                for name, _ in WIRE_FEEDS}
    return lambda url: next(payloads[name] for name, feed_url in WIRE_FEEDS if feed_url == url)


def _seed_identity(conn):
    rows = [("merck and", "Merck & Co., Inc.", "MRK", 310158),
            ("moderna", "Moderna, Inc.", "MRNA", 1682852),
            ("united", "UNITED THERAPEUTICS Corp", "UTHR", 1082554),
            ("nasdaq", "NASDAQ, INC.", "NDAQ", 1120193)]
    for norm, sponsor, ticker, cik in rows:
        conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                     (norm, sponsor, ticker, cik, 0.9, "SEC-v2C-equity", "2026-10-20"))
    conn.commit()


def test_joint_release_attributes_to_watched_issuer_only(tmp_path):
    conn = db.connect(tmp_path / "wire.db")
    _seed_identity(conn)
    items = [(JOINT_HEADLINE, "https://www.businesswire.com/news/home/20261022/interpath", JOINT_SUMMARY, STAMP)]
    result = poll_wire_feeds(conn, fetch=_fetch(items), now=NOW)
    assert result["signals_seen"] >= 1
    events = [tuple(row) for row in conn.execute(
        "SELECT ticker, severity, change_type FROM change_events WHERE source_type='wire'")]
    assert ("MRNA", "high", "wire_release_signal") in events
    tickers = {row[0] for row in events}
    assert "MRK" not in tickers       # unwatched partner adds no alert noise
    assert "UTHR" not in tickers      # "United States" is not United Therapeutics
    assert "NDAQ" not in tickers      # "(Nasdaq: MRNA)" is not the exchange's own stock
    payload = conn.execute(
        "SELECT new_value FROM change_events WHERE ticker='MRNA'").fetchone()
    assert "INTerpath-001" in json.loads(payload[0])["headline"]


def test_joint_release_without_watched_issuer_keeps_unattributed_event(tmp_path):
    conn = db.connect(tmp_path / "wire.db")
    _seed_identity(conn)
    items = [("Pfizer and Merck Will Present Phase 3 Combination Data at ESMO 2026",
              "https://www.businesswire.com/news/home/20261022/other",
              "Pfizer (NYSE: PFE) and Merck (NYSE: MRK) today announced results.", STAMP)]
    poll_wire_feeds(conn, fetch=_fetch(items), now=NOW)
    events = [tuple(row) for row in conn.execute(
        "SELECT ticker, severity, change_type FROM change_events WHERE source_type='wire'")]
    assert events == [(None, "medium", "wire_release_signal")]
