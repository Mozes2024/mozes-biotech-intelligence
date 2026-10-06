"""Tests for the breaking-catalyst hot lane: outbox, latency, materiality, TTL, CT signals."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from mozes import db
from mozes.alert_dispatch import dispatch_pending, enqueue_change, sync_new_changes
from mozes.latency_metrics import summary
from mozes.live_monitor import monitor_study, record_change
from mozes.materiality import classify_outcome
from mozes.sec_http import enable_hot_mode, ttl_for


NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def test_outcome_classification_is_deterministic():
    assert classify_outcome("Company met its primary endpoint with statistically significant results")["polarity"] == "positive"
    assert classify_outcome("The trial did not meet the primary endpoint; CRL received")["polarity"] == "negative"
    assert classify_outcome("Numerical trend observed; secondary endpoints only")["polarity"] == "mixed"
    assert classify_outcome("Company hosts investor day")["polarity"] == "unknown"


def test_sec_hot_path_bypasses_900s_cache():
    url = "https://data.sec.gov/submissions/CIK0001234567.json"
    enable_hot_mode(False)
    assert ttl_for(url) == 900
    enable_hot_mode(True)
    assert ttl_for(url) == 60
    assert ttl_for("https://www.sec.gov/Archives/edgar/data/1/000/primary.htm") == 365 * 86400
    enable_hot_mode(False)


def test_outbox_exactly_once_and_stage1_dispatch(tmp_path):
    conn = db.connect(tmp_path / "alerts.db")
    change_id = record_change(
        conn, ticker="SLS", change_type="company_release_signal", previous_value=None,
        new_value={"headline": "SELLAS announces Phase 3 topline", "published_at": NOW.isoformat()},
        source_url="https://ir.sellaslifesciences.com/news/1", source_type="company_ir",
        severity="medium", verification_state="investigation_only",
        identity=["company_release", "SLS", "https://ir.sellaslifesciences.com/news/1"],
    )
    # record_change already enqueues Stage-1 once; a second enqueue must be a no-op.
    assert conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0] == 1
    assert enqueue_change(conn, change_id, channels=("log",)) == []
    # Dispatch clock must be at/after send_after (written with db.utcnow()).
    later = datetime.now(timezone.utc) + timedelta(seconds=1)
    sent = dispatch_pending(conn, now=later)
    assert sent["sent"] == 1
    resent = dispatch_pending(conn, now=later)
    assert resent["sent"] == 0
    assert conn.execute("SELECT status FROM alert_outbox").fetchone()[0] == "sent"
    assert conn.execute("SELECT COUNT(*) FROM alert_deliveries").fetchone()[0] == 1
    metrics = summary(conn)
    assert metrics["detection_latency_seconds"]["n"] >= 1


def test_stage1_precedes_stage2(tmp_path):
    conn = db.connect(tmp_path / "stages.db")
    change_id = record_change(
        conn, ticker="MRNA", change_type="sec_material_filing", previous_value=None,
        new_value={"form": "8-K", "headline": "positive topline"},
        source_url="https://www.sec.gov/Archives/edgar/data/1/a.htm", source_type="sec",
        severity="high", identity=["sec", "MRNA", "acc-1"],
    )
    assert conn.execute("SELECT stage FROM alert_outbox").fetchone()[0] == "stage1"
    stage2 = enqueue_change(conn, change_id, stage="stage2", channels=("log",))
    assert stage2
    rows = conn.execute("SELECT stage FROM alert_outbox ORDER BY stage").fetchall()
    assert [r["stage"] for r in rows] == ["stage1", "stage2"]


def test_material_discovery_retries_before_24h(tmp_path, monkeypatch):
    from mozes.news_signals import verify_discovered
    from mozes import refresh
    import time
    conn = db.connect(tmp_path / "retry.db")
    issuer = {"ticker": "ZNTL", "sponsor": "Zentalis", "cik": "1", "source": "SEC-v2C-equity",
              "material": True, "headline": "Zentalis Phase 3 topline data"}
    conn.execute(
        "INSERT INTO sponsor_ticker_map VALUES (?,?,?,?,?,?,?)",
        ("zentalis", "Zentalis", "ZNTL", "1", 0.99, "SEC-v2C-equity", NOW.isoformat()),
    )
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps({"studies": []}).encode()

    first = verify_discovered(conn, [issuer], fetch=fetch, now=NOW, deadline=time.monotonic() + 30)
    assert first["checked"] == ["ZNTL"]
    blocked = verify_discovered(conn, [issuer], fetch=fetch, now=NOW + timedelta(seconds=10),
                                deadline=time.monotonic() + 30)
    assert blocked["checked"] == []
    # After the first material slot (~1 minute), another attempt is allowed.
    retried = verify_discovered(conn, [issuer], fetch=fetch, now=NOW + timedelta(minutes=2),
                                deadline=time.monotonic() + 30)
    assert retried["checked"] == ["ZNTL"]
    assert len(calls) == 2


def test_primary_completion_type_change_is_tracked(tmp_path):
    conn = db.connect(tmp_path / "ct.db")

    def study(pc_type="ESTIMATED"):
        return {"protocolSection": {
            "identificationModule": {"nctId": "NCT99999999"},
            "statusModule": {
                "overallStatus": "RECRUITING",
                "primaryCompletionDateStruct": {"date": "2026-12-01", "type": pc_type},
                "completionDateStruct": {"date": "2027-01-01"},
                "lastUpdatePostDateStruct": {"date": "2026-09-01"},
                "resultsFirstPostDateStruct": {"date": None},
                "whyStopped": None,
            },
            "designModule": {"enrollmentInfo": {"count": 100}},
        }}

    assert monitor_study(conn, study("ESTIMATED"), ticker="TEST") == []
    ids = monitor_study(conn, study("ACTUAL"), ticker="TEST")
    assert len(ids) == 1
    row = conn.execute("SELECT change_type,new_value FROM change_events").fetchone()
    assert row["change_type"] == "ctgov_primary_completion_type_changed"
    assert json.loads(row["new_value"]) == "ACTUAL"


def test_html_ir_fallback_when_no_rss(tmp_path):
    from mozes.news_signals import poll_official_feeds
    conn = db.connect(tmp_path / "ir.db")
    issuer = {"ticker": "SLS", "sponsor": "SELLAS", "cik": "1"}

    page = {"html": b'<html><body><a href="/press-releases">Press Releases</a>'
                    b'<a href="/news-releases/sellas-reports-second-quarter-2026-results">'
                    b'SELLAS Reports Second Quarter 2026 Financial Results</a></body></html>'}

    def fetch(url):
        if url.endswith("/"):
            return page["html"]
        raise AssertionError(url)

    baseline = poll_official_feeds(conn, [issuer], fetch=fetch, now=NOW)
    assert baseline["signals_seen"] == 0  # first sighting of an undated index is a baseline
    obs = json.loads(conn.execute(
        "SELECT value_json FROM monitor_observations WHERE observation_key=?",
        ("official_feed:SLS",)).fetchone()[0])
    assert obs["status"] == "html_fallback" and obs["index_links"] == 1  # nav link filtered

    page["html"] = page["html"].replace(
        b"</body>", b'<a href="/news-releases/sellas-announces-phase-3-regal-topline-results">'
                    b'SELLAS Announces Phase 3 REGAL Topline Results</a></body>')
    result = poll_official_feeds(conn, [issuer], fetch=fetch, now=NOW)
    assert result["signals_seen"] == 1
    headline = json.loads(conn.execute("SELECT new_value FROM change_events").fetchone()[0])["headline"]
    assert headline == "SELLAS Announces Phase 3 REGAL Topline Results"


def test_sync_new_changes_dispatches_without_pages(tmp_path):
    conn = db.connect(tmp_path / "sync.db")
    record_change(
        conn, ticker="SLS", change_type="fda_release_signal", previous_value=None,
        new_value={"headline": "FDA approves therapy", "published_at": NOW.isoformat()},
        source_url="https://www.fda.gov/news/1", source_type="fda", severity="high",
        identity=["fda", "x"],
    )
    out = sync_new_changes(conn)
    assert out["dispatch"]["sent"] >= 1
