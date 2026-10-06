"""v2G hot-lane hardening: source isolation, outbox lease recovery, real-feed fixtures,
watchlist-only P1, single push owner, cross-source consolidation, classifier corpus."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mozes import db, hot_monitor
from mozes.alert_dispatch import (
    configured_channels, corroborations, dispatch_pending, enqueue_change, headline_similarity,
)
from mozes.ir_registry import resolve_alias
from mozes.live_monitor import record_change
from mozes.materiality import alert_priority, classify_outcome
from mozes.primary_feeds import (
    NASDAQ_HALTS_FEED, WIRE_FEEDS, _parse_rss_items, parse_nasdaq_halts, poll_nasdaq_halts, poll_wire_feeds,
)

FIXTURES = Path(__file__).parent / "fixtures"
FEEDS = FIXTURES / "feeds"
CAPTURED = datetime(2026, 10, 6, 15, 5, tzinfo=timezone.utc)
GMAB_RELEASE = datetime(2026, 10, 5, 18, 50, tzinfo=timezone.utc)


def _feed(name):
    return (FEEDS / f"{name}.xml").read_bytes()


@pytest.fixture(autouse=True)
def _no_push_env(monkeypatch):
    for key in ("MOZES_NTFY_URL", "MOZES_WEBHOOK_URL", "GITHUB_ACTIONS", "MOZES_PUSH_FROM_ACTIONS",
                "MOZES_ALERT_MIN_PRIORITY", "SEC_USER_AGENT"):
        monkeypatch.delenv(key, raising=False)


# 1 — one failing source must not drop the pass -------------------------------------------

def test_failing_source_is_isolated_and_dispatch_still_runs(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "hot.db")
    calls = []

    def boom(*_a, **_k):
        raise RuntimeError("fda down")

    monkeypatch.setattr(hot_monitor, "poll_fda_feeds", boom)
    monkeypatch.setattr(hot_monitor, "poll_wire_feeds", lambda conn, fetch: {"signals_seen": 0, "errors": 0})
    monkeypatch.setattr(hot_monitor, "poll_official_feeds",
                        lambda *a, **k: {"signals_seen": 0, "errors": [{"ticker": "X", "error": "timeout"}]})
    monkeypatch.setattr(hot_monitor, "poll_nasdaq_halts",
                        lambda conn, fetch, watch_tickers: calls.append("halts") or {"signals_seen": 0, "errors": 0})
    monkeypatch.setattr(hot_monitor, "dispatch_pending", lambda conn: calls.append("dispatch") or {"sent": 0})

    result = hot_monitor.run_hot_pass(conn, include_sec=False, fetch=lambda url: b"")
    assert result["status"] == "PARTIAL"
    assert "fda" in result["errors"]
    assert calls == ["halts", "dispatch"]


def test_all_sources_failing_is_failed(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "hot.db")

    def boom(*_a, **_k):
        raise RuntimeError("down")

    for name in ("seed_priority_watches", "poll_fda_feeds", "poll_wire_feeds", "poll_official_feeds",
                 "poll_nasdaq_halts", "sync_new_changes", "dispatch_pending", "latency_summary"):
        monkeypatch.setattr(hot_monitor, name, boom)
    assert hot_monitor.run_hot_pass(conn, include_sec=False, fetch=lambda url: b"")["status"] == "FAILED"


# 2 — a crashed sender must not strand rows in 'sending' -----------------------------------

def _queued_alert(conn):
    change_id = record_change(
        conn, ticker="GMAB", change_type="company_release_signal", previous_value=None,
        new_value={"headline": "Genmab announces positive topline results"},
        source_url="https://ir.genmab.com/news/1", source_type="company_ir", severity="high",
        identity=["company_release", "GMAB", "1"],
    )
    return change_id, conn.execute("SELECT alert_id FROM alert_outbox").fetchone()[0]


def test_stale_sending_row_is_recovered_after_lease(tmp_path):
    conn = db.connect(tmp_path / "lease.db")
    _, alert_id = _queued_alert(conn)
    now = datetime.now(timezone.utc) + timedelta(seconds=1)

    def crash(_payload):
        raise SystemExit("worker killed mid-send")

    with pytest.raises(SystemExit):
        dispatch_pending(conn, now=now, sender={"log": crash})
    assert conn.execute("SELECT status FROM alert_outbox").fetchone()[0] == "sending"

    early = dispatch_pending(conn, now=now + timedelta(seconds=30))
    assert early["sent"] == 0 and early["recovered"]["retried"] == 0

    late = dispatch_pending(conn, now=now + timedelta(minutes=3))
    assert late["recovered"]["retried"] == 1
    assert late["sent"] == 1
    status, attempts = conn.execute("SELECT status,attempts FROM alert_outbox WHERE alert_id=?", (alert_id,)).fetchone()
    assert (status, attempts) == ("sent", 2)


def test_stale_sending_at_max_attempts_goes_dead(tmp_path):
    conn = db.connect(tmp_path / "dead.db")
    _, alert_id = _queued_alert(conn)
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn.execute("UPDATE alert_outbox SET status='sending', attempts=8, send_after=? WHERE alert_id=?", (past, alert_id))
    conn.commit()
    out = dispatch_pending(conn)
    assert out["recovered"]["dead"] == 1
    assert conn.execute("SELECT status FROM alert_outbox").fetchone()[0] == "dead"


# 3 — real feed fixtures ------------------------------------------------------------------

def test_nasdaq_fixture_uses_ndaq_namespace():
    halts = parse_nasdaq_halts(_feed("nasdaq_halts"), now=CAPTURED)
    by_symbol = {}
    for halt in halts:
        by_symbol.setdefault(halt["ticker"], []).append(halt)
    avbp = by_symbol["AVBP"][0]
    assert avbp["reason_code"] == "T3"
    assert avbp["halted_at"] == "2026-10-06T11:55:00+00:00"
    assert avbp["issue_name"].startswith("ArriVent")
    assert len(by_symbol["XHG"]) == 5
    # BLFS T12 from the previous evening is still halted (no resumption) and stays visible.
    assert by_symbol["BLFS"][0]["reason_code"] == "T12"
    # Multi-year-old entries are not reprocessed.
    assert "SVA" not in by_symbol


def test_nasdaq_halts_record_watched_news_and_pause_codes_only(tmp_path):
    conn = db.connect(tmp_path / "halts.db")
    raw = _feed("nasdaq_halts")
    result = poll_nasdaq_halts(conn, fetch=lambda url: raw, now=CAPTURED, watch_tickers=["AVBP", "XHG", "BLFS"])
    rows = conn.execute("SELECT ticker,change_type,severity,metadata_json FROM change_events ORDER BY ticker").fetchall()
    kinds = {(r["ticker"], r["change_type"], r["severity"]) for r in rows}
    assert ("AVBP", "nasdaq_halt_signal", "critical") in kinds
    assert ("BLFS", "nasdaq_halt_signal", "critical") in kinds
    assert ("XHG", "nasdaq_volatility_pause", "high") in kinds
    # Each XHG pause at a distinct time is a distinct event (identity includes code + time).
    assert sum(1 for r in rows if r["ticker"] == "XHG") == 5
    assert {r["ticker"] for r in rows} == {"AVBP", "BLFS", "XHG"}
    assert result["signals_seen"] == 7
    again = poll_nasdaq_halts(conn, fetch=lambda url: raw, now=CAPTURED, watch_tickers=["AVBP", "XHG", "BLFS"])
    assert again["signals_seen"] == 0


@pytest.mark.parametrize("name", [name for name, _url in WIRE_FEEDS])
def test_wire_fixtures_parse(name):
    raw = _feed(name)
    items = _parse_rss_items(raw, now=CAPTURED, max_age_hours=48)
    assert items, name
    assert all(item["title"] and item["url"].startswith("http") for item in items)


def test_gmab_oct5_release_replays_as_single_p1_alert(tmp_path):
    """The Genmab EPCORE DLBCL-2 release that was missed on 2026-10-05, replayed from real feeds."""
    conn = db.connect(tmp_path / "gmab.db")
    feeds = {url: _feed(name) for name, url in WIRE_FEEDS}
    result = poll_wire_feeds(conn, fetch=lambda url: feeds[url], now=GMAB_RELEASE + timedelta(minutes=5),
                             resolve_headline=lambda conn, headline: None)
    assert result["errors"] == 0
    gmab = conn.execute("SELECT change_id,new_value FROM change_events WHERE ticker='GMAB'").fetchall()
    assert len(gmab) == 1  # same URL in two Business Wire feeds is one change
    assert json.loads(gmab[0]["new_value"])["outcome"]["polarity"] == "positive"
    alerts = conn.execute("SELECT payload_json FROM alert_outbox WHERE change_id=?", (gmab[0]["change_id"],)).fetchall()
    assert len(alerts) == 1
    assert json.loads(alerts[0]["payload_json"])["priority"] == "P1"


def test_fda_fixtures_from_runner_record_without_paging(tmp_path):
    """fda.gov blocks the dev host; these samples were captured by the feed-probe workflow."""
    from mozes.primary_feeds import FDA_FEEDS, poll_fda_feeds
    conn = db.connect(tmp_path / "fda.db")
    feeds = {url: _feed(name) for name, url in FDA_FEEDS}
    result = poll_fda_feeds(conn, fetch=lambda url: feeds[url], now=CAPTURED,
                            resolve_headline=lambda conn, headline: None)
    assert result["errors"] == 0 and result["signals_seen"] >= 1
    unattributed = conn.execute("SELECT COUNT(*) FROM change_events WHERE ticker IS NULL").fetchone()[0]
    queued = {r[0] for r in conn.execute(
        "SELECT o.change_id FROM alert_outbox o JOIN change_events c USING(change_id) WHERE c.ticker IS NULL")}
    assert unattributed >= 1 and queued == set()


def test_alias_resolution_is_unique_or_nothing():
    assert resolve_alias("Genmab and AbbVie Announce Epcoritamab Data")["ticker"] == "GMAB"
    assert resolve_alias("FDA approves Rezdiffra label update")["ticker"] == "MDGL"
    assert resolve_alias("Casgevy real-world data") is None  # shared by VRTX and CRSP
    assert resolve_alias("Unrelated company raises capital") is None


# 4 — P1 only for watchlist tickers; unattributed signals never page ----------------------

def test_p1_requires_watched_ticker():
    material = {"material": True}
    assert alert_priority("fda_release_signal", "high", material, ticker=None, watched=False) == "P3"
    assert alert_priority("fda_release_signal", "high", material, ticker="ZZZZ", watched=False) == "P2"
    assert alert_priority("fda_release_signal", "high", material, ticker="GMAB", watched=True) == "P1"
    assert alert_priority("nasdaq_halt_signal", "critical", {}, ticker="ZZZZ", watched=False) == "P2"
    assert alert_priority("nasdaq_volatility_pause", "high", {}, ticker="GMAB", watched=True) == "P2"
    # A non-material headline-only release is browsing, not an alert, even for a watched name.
    assert alert_priority("company_release_signal", "medium", {"material": False}, ticker="GMAB", watched=True) == "P3"


def test_unattributed_fda_release_is_recorded_but_not_queued(tmp_path):
    conn = db.connect(tmp_path / "fda.db")
    record_change(
        conn, ticker=None, change_type="fda_release_signal", previous_value=None,
        new_value={"headline": "FDA approves first generic of a widely used drug"},
        source_url="https://www.fda.gov/news/x", source_type="fda", severity="medium",
        identity=["fda", "x"],
    )
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0] == 0


def test_8k_form_alone_is_not_material():
    assert classify_outcome("8-K")["material"] is False
    assert classify_outcome("Form 6-K Report of Foreign Private Issuer")["material"] is False


# 6 — one push owner ------------------------------------------------------------------------

def test_actions_runs_never_push(monkeypatch):
    monkeypatch.setenv("MOZES_NTFY_URL", "https://ntfy.example/topic")
    assert configured_channels() == ("ntfy",)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert configured_channels() == ("log",)
    monkeypatch.setenv("MOZES_PUSH_FROM_ACTIONS", "1")
    assert configured_channels() == ("ntfy",)


def test_workflows_do_not_receive_push_secrets():
    root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    for path in root.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert "MOZES_NTFY_URL" not in text and "MOZES_WEBHOOK_URL" not in text, path.name


# 7 — cross-source consolidation -------------------------------------------------------------

def test_same_story_from_second_source_is_linked_not_repushed(tmp_path):
    conn = db.connect(tmp_path / "merge.db")
    first = record_change(
        conn, ticker="GMAB", change_type="company_release_signal", previous_value=None,
        new_value={"headline": "Genmab Announces Epcoritamab Plus R-CHOP Demonstrates Statistically Significant PFS"},
        source_url="https://ir.genmab.com/news/epcore", source_type="company_ir", severity="high",
        identity=["company_release", "GMAB", "epcore"],
    )
    second = record_change(
        conn, ticker="GMAB", change_type="wire_release_signal", previous_value=None,
        new_value={"headline": "Genmab and AbbVie Announce Epcoritamab in Combination with R-CHOP Demonstrates "
                               "Statistically Significant Improvement in Progression-Free Survival"},
        source_url="https://www.businesswire.com/news/home/x", source_type="wire", severity="high",
        identity=["wire", "x"],
    )
    unrelated = record_change(
        conn, ticker="GMAB", change_type="wire_release_signal", previous_value=None,
        new_value={"headline": "FDA Approves Genmab's Rina-S for Platinum-Resistant Ovarian Cancer"},
        source_url="https://www.businesswire.com/news/home/y", source_type="wire", severity="high",
        identity=["wire", "y"],
    )
    queued = {r[0] for r in conn.execute("SELECT change_id FROM alert_outbox")}
    assert first in queued and unrelated in queued and second not in queued
    links = corroborations(conn, first)
    assert [link["change_id"] for link in links] == [second]
    assert links[0]["source_type"] == "wire"


def test_headline_similarity_scale():
    assert headline_similarity("Acme Met Primary Endpoint in Phase 3 ALPHA",
                               "Acme Therapeutics Announces Phase 3 ALPHA Met Primary Endpoint") >= 0.55
    assert headline_similarity("Acme Met Primary Endpoint", "Acme Names New CFO") < 0.55


# 5 — classifier regression corpus ----------------------------------------------------------

def _corpus():
    rows = []
    for line in (FIXTURES / "headline_corpus.tsv").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        polarity, material, origin, strictness, headline = line.split("\t")
        rows.append((polarity, material == "1", origin, strictness, headline))
    return rows


def test_corpus_size_and_real_share():
    rows = _corpus()
    assert 50 <= len(rows) <= 120
    assert sum(1 for r in rows if r[2].startswith("feed:")) >= 25


@pytest.mark.parametrize("polarity,material,origin,strictness,headline",
                         [r for r in _corpus() if r[3] == "must"])
def test_corpus_must_rows(polarity, material, origin, strictness, headline):
    result = classify_outcome(headline)
    assert (result["polarity"], result["material"]) == (polarity, material), headline


def test_corpus_has_no_sign_flips_and_high_agreement():
    rows = _corpus()
    flips = [h for p, _m, _o, _s, h in rows
             if {p, classify_outcome(h)["polarity"]} == {"positive", "negative"}]
    assert flips == []
    agree = sum(1 for p, m, _o, _s, h in rows
                if (classify_outcome(h)["polarity"], classify_outcome(h)["material"]) == (p, m))
    assert agree / len(rows) >= 0.95
