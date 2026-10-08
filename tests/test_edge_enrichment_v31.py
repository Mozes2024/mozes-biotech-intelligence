import json
from pathlib import Path

import pytest

from mozes import db
from mozes.alert_dispatch import build_stage1_payload
from mozes.edge_enrichment import process, ack_payload


def setup(tmp_path):
    conn = db.connect(tmp_path / "edge.db")
    db.upsert_watch(conn, "ZZZZ", cik="123", company="Novel Bio", source="dynamic_news_discovery")
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("novel", "Novel Bio", "ZZZZ", "123", .99, "SEC-v2C-equity", "2026-10-08"))
    return conn


def event(i=1):
    return {"edge_event_id": "EDGE-" + str(i).zfill(24), "source": "businesswire", "source_url":
            "https://www.businesswire.com/news/home/202610080001/en/Novel-Bio", "ticker": "ZZZZ", "cik": "123",
            "headline": "Novel Bio announces topline Phase 2 results", "published_at": "2026-10-08T08:00:00Z",
            "analysis_status": "complete", "material": 1}


def test_distinct_edge_ids_reuse_stable_change_and_alert_without_duplicate_delivery(tmp_path):
    conn = setup(tmp_path)
    calls = []
    def fetch(url):
        calls.append(url)
        return "<p>Novel Bio Phase 2 trial met its primary endpoint.</p>"
    change = process(conn, event(), fetch=fetch, github_run_id="42")
    assert process(conn, event(), fetch=lambda _: pytest.fail("already durable event refetched"), github_run_id="43") == change
    assert process(conn, event(2), fetch=fetch, github_run_id="44") == change
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM edge_event_links").fetchone()[0] == 2
    payload = build_stage1_payload(conn, change)
    assert payload["edge_event_ids"] == [event()["edge_event_id"], event(2)["edge_event_id"]]
    stored = json.loads(conn.execute("SELECT payload_json FROM alert_outbox").fetchone()[0])
    assert stored["edge_event_ids"] == payload["edge_event_ids"]
    ack = ack_payload(conn, event()["edge_event_id"], github_run_id="43")
    assert ack["change_id"] == change and ack["github_run_id"] == "43"
    assert len(ack["alert_ids"]) == 1 and ack["delivery"][0]["status"] == "pending"
    assert conn.execute("SELECT COUNT(*) FROM source_archive").fetchone()[0] == 2


def test_failed_primary_fetch_has_no_durable_ack(tmp_path):
    conn = setup(tmp_path)
    def broken(_):
        raise OSError("network failed")
    with pytest.raises(OSError):
        process(conn, event(), fetch=broken, github_run_id="42")
    with pytest.raises(RuntimeError, match="no durable"):
        ack_payload(conn, event()["edge_event_id"])


def test_official_wire_http_link_is_fetched_only_over_https(tmp_path, monkeypatch):
    conn = setup(tmp_path)
    original = event()["source_url"].replace("https:", "http:")
    monkeypatch.setenv("MOZES_HOT_SOURCE_URL", original)
    calls = []
    process(conn, {**event(), "source_url": original},
            fetch=lambda url: calls.append(url) or "Phase 2 trial met its primary endpoint.", github_run_id="42")
    assert calls == [event()["source_url"]]
    monkeypatch.delenv("MOZES_HOT_SOURCE_URL")
    with pytest.raises(ValueError, match="untrusted"):
        process(conn, {**event(2), "source_url": "http://attacker.example/"}, fetch=lambda _: "content", github_run_id="42")


def test_canonical_edge_source_and_issuer_are_required(tmp_path, monkeypatch):
    conn = setup(tmp_path)
    with pytest.raises(ValueError, match="untrusted"):
        process(conn, {**event(), "source_url": "https://attacker.example/"}, fetch=lambda _: "content", github_run_id="42")
    with pytest.raises(ValueError, match="issuer"):
        process(conn, {**event(), "cik": "999"}, fetch=lambda _: "content", github_run_id="42")
    monkeypatch.setenv("MOZES_HOT_TICKER", "OTHER")
    with pytest.raises(ValueError, match="differs"):
        process(conn, event(), fetch=lambda _: "content", github_run_id="42")


def test_filtered_enrichment_still_has_trace_without_relaxing_alert_policy(tmp_path):
    conn = setup(tmp_path)
    change = process(conn, event(), fetch=lambda _: "Novel Bio announces routine employee training.", github_run_id="42")
    ack = ack_payload(conn, event()["edge_event_id"])
    assert ack["change_id"] == change
    assert ack["alert_ids"] == []
    assert conn.execute("SELECT decision FROM alert_enqueue_decisions WHERE change_id=?", (change,)).fetchone()[0] == "filtered"


def test_workflow_ack_follows_durable_upload_and_preserves_inputs():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/lightweight-monitor.yml").read_text()
    assert "queue: max" in workflow and "cancel-in-progress: false" in workflow
    assert "MOZES_EDGE_EVENT_ID: ${{ inputs.edge_event_id }}" in workflow
    assert workflow.index("python -m mozes.edge_enrichment process") < workflow.index("actions/upload-artifact@v4")
    assert workflow.index("actions/upload-artifact@v4") < workflow.index("python -m mozes.edge_enrichment ack")
