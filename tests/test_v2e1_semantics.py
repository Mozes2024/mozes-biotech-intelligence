from datetime import date

from mozes import db
from mozes.discovery import discover
from mozes.live_prices import refresh_live_prices
from mozes import payload_v3


def test_discovery_time_budget_is_bounded_not_provider_failure():
    calls = []

    def fetch(url):
        calls.append(url)
        return {"studies": [], "nextPageToken": "later"}

    rows, meta = discover(fetcher=fetch, today=date(2026, 10, 1), budget_seconds=0, return_metadata=True)
    assert rows == [] and calls == []
    assert meta["truncated"] is True
    assert meta["stop_reason"] == "time_budget"
    assert meta["next_page_token_present"] is False


def test_single_live_price_failure_is_enrichment_incomplete(tmp_path):
    conn = db.connect(tmp_path / "prices.db")
    db.upsert_watch(conn, "AAA", "Alpha")

    def fetch(ticker, start, end):
        if ticker == "AAA":
            raise RuntimeError("one ticker unavailable")
        return [{"date": "2026-09-30", "close": 100, "volume": 1000}]

    result = refresh_live_prices(conn, today=date(2026, 10, 1), fetcher=fetch)
    assert result["status"] == "INCOMPLETE"
    assert result["failed_tickers"] == ["AAA"]
    assert result["systemic_failure"] is False


def test_health_separates_core_monitor_from_enrichment(monkeypatch):
    monkeypatch.setattr(payload_v3, "operation_health", lambda conn: {"modules": {
        "monitor": {"status": "OK", "details": {}},
        "prices": {"status": "INCOMPLETE", "details": {"failed_tickers": ["AAA"]}},
        "financials": {"status": "SKIPPED", "details": {"reason": "not configured"}},
    }})
    health = payload_v3._health(object(), [], {"status": "OK"}, {"status": "OK"})
    assert health["primary_catalyst_monitoring"]["healthy"] is True
    assert health["severity"]["red"] == []
    assert [x["module"] for x in health["severity"]["amber"]] == ["prices"]
    assert [x["module"] for x in health["severity"]["info"]] == ["financials", "identity"]
