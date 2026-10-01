import json
import hashlib
from datetime import date
from pathlib import Path

from mozes import db
from mozes.historical import backfill_prices, import_bundle, readiness_summary
from mozes.ingest.nasdaq_trader import NASDAQ_URL, OTHER_URL, parse_directory
from mozes.payload_v3 import build
from mozes.radar import bootstrap_database
from mozes.security import audit_current_universe, audit_watch_universe, tradability


def test_both_official_directories_preserve_financial_status_and_filter_nonstocks(tmp_path):
    nasdaq = parse_directory("Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
                             "BIO|Bio Inc - Common Stock|Q|N|D|100|N|N\n"
                             "TEST|Test Issue|Q|Y|N|100|N|N\n"
                             "FUND|ETF|Q|N|N|100|Y|N\n", NASDAQ_URL)
    other = parse_directory("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
                            "PHAR|Pharming N.V. ADS|N|PHAR|N|100|N|PHAR\n", OTHER_URL)
    assert nasdaq[0]["financial_status"] == "D" and other[0]["exchange"] == "N"
    conn = db.connect(tmp_path / "x.db")
    db.upsert_watch(conn, "BIO"); db.upsert_watch(conn, "TEST")
    db.upsert_watch(conn, "FUND"); db.upsert_watch(conn, "PHAR")
    rows = audit_watch_universe(conn, nasdaq + other, provider="nasdaq")
    assert {r["ticker"] for r in rows if r["status"] == "ACTIVE"} == {"BIO", "PHAR"}
    assert conn.execute("SELECT financial_status FROM security_listing_audit WHERE ticker='BIO'").fetchone()[0] == "D"


def test_auto_audit_uses_keyless_provider_and_preserves_terminal_override(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "x.db")
    db.upsert_watch(conn, "BIO"); db.upsert_watch(conn, "OLD")
    db.upsert_security_lifecycle(conn, "OLD", status="RENAMED", successor_ticker="NEW", source_url="https://sec.test/action", source_type="sec_8k")
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setattr("mozes.ingest.nasdaq_trader.fetch_current_listings", lambda: [
        {"ticker":"BIO","name":"Bio","source_url":NASDAQ_URL,"test_issue":"N","etf":"N"},
        {"ticker":"OLD","name":"Old","source_url":NASDAQ_URL,"test_issue":"N","etf":"N"}])
    result = audit_current_universe(conn)
    assert result == {"provider":"nasdaq","audited":2,"active":1,"unknown":0,"terminal":1}
    assert tradability(conn, "OLD")["status"] == "RENAMED"


def test_historical_only_tickers_do_not_enter_current_watch_universe(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    live = {e["ticker"] for e in db.load_events(conn, "live")}
    assert {r["ticker"] for r in db.watch_rows(conn)} == live
    assert "KRTX" not in live


def test_stale_conference_is_in_audit_queue_and_quality_counts(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    payload = build(conn, date(2026, 10, 1))
    assert "QTTB-CONF" not in {e["id"] for e in payload["live"]}
    stale = {e["id"]: e for e in payload["stale"]}
    assert stale["QTTB-CONF"]["age_days"] >= 90
    assert payload["summary"]["stale_events"] == len(payload["stale"])
    assert payload["summary"]["historical_cases"] == 21


def test_starter_cases_are_separate_blinded_and_price_ready(tmp_path):
    root = Path(__file__).resolve().parents[1]
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    bundle = root / "mozes" / "data" / "historical_starter.json"
    manifest = json.loads((root / "mozes" / "data" / "starter_prices" / "manifest.json").read_text(encoding="utf-8"))
    for ticker, record in manifest["files"].items():
        data = (root / "mozes" / "data" / "starter_prices" / f"{ticker}.csv").read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
    import_bundle(conn, bundle)
    import_bundle(conn, bundle)
    for ticker, case_id in (("KOD", "PIT-KOD-DAYBREAK-20260928"),
                            ("QTTB", "PIT-QTTB-SIGNALAA-20260713"),
                            ("PHAR", "PIT-PHAR-JOENJA-20260911")):
        prices = root / "mozes" / "data" / "starter_prices"
        backfill_prices(conn, case_id, "csv", stock_file=str(prices / f"{ticker}.csv"),
                        benchmark_file=str(prices / "XBI.csv"), source_url="https://query1.finance.yahoo.com/v8/finance/chart/")
        snapshots = db.feature_snapshot_rows(conn, case_id)
        labels = db.outcome_label_rows(conn, case_id)
        event_at = next(c["event_at"] for c in db.historical_case_rows(conn) if c["case_id"] == case_id)
        assert all(s["as_of"] < event_at for s in snapshots)
        assert all("clinical" not in json.loads(s["payload"]) and "regulatory" not in json.loads(s["payload"]) for s in snapshots)
        assert len(labels) == 1
    summary = readiness_summary(conn)
    assert (summary["cases"], summary["research_ready"], summary["runup_ready"], summary["hold_ready"]) == (24, 3, 3, 0)
