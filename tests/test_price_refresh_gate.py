from datetime import date, datetime, timedelta, timezone

from mozes import db, live_prices
from mozes.intelligence_store import ensure_schema, operation_finish, operation_start
from mozes.live_prices import maybe_refresh_live_prices, refresh_live_prices


def _conn(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    ensure_schema(conn)
    return conn


def _record_prices_op(conn, status, hours_ago):
    rid = operation_start(conn, "prices")
    operation_finish(conn, rid, status, {})
    back = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    conn.execute("UPDATE v2c_operation_runs SET started_at=? WHERE operation='prices'", (back,))
    conn.commit()


def test_price_refresh_skips_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("MOZES_PRICE_REFRESH", "0")
    out = maybe_refresh_live_prices(_conn(tmp_path))
    assert out["status"] == "SKIPPED" and out["reason"] == "MOZES_PRICE_REFRESH disabled"


def test_price_refresh_runs_by_default_and_never_ran(tmp_path, monkeypatch):
    monkeypatch.delenv("MOZES_PRICE_REFRESH", raising=False)
    monkeypatch.setattr(live_prices, "refresh_live_prices", lambda conn, **kw: {"status": "OK"})
    assert maybe_refresh_live_prices(_conn(tmp_path))["status"] == "OK"


def test_price_refresh_skips_after_recent_success(tmp_path, monkeypatch):
    monkeypatch.delenv("MOZES_PRICE_REFRESH", raising=False)
    conn = _conn(tmp_path)
    _record_prices_op(conn, "OK", 2)
    monkeypatch.setattr(live_prices, "refresh_live_prices",
                        lambda conn, **kw: (_ for _ in ()).throw(AssertionError("must not run")))
    out = maybe_refresh_live_prices(conn)
    assert out["status"] == "SKIPPED" and out["reason"] == "recent_refresh"


def test_price_refresh_skipped_rows_do_not_extend_gate(tmp_path, monkeypatch):
    """Regression: a SKIPPED gate decision is itself recorded as a prices op;
    counting it would re-arm the interval every monitor cycle and deadlock."""
    monkeypatch.delenv("MOZES_PRICE_REFRESH", raising=False)
    conn = _conn(tmp_path)
    rid_ok = operation_start(conn, "prices")
    operation_finish(conn, rid_ok, "OK", {})
    rid_skip = operation_start(conn, "prices")
    operation_finish(conn, rid_skip, "SKIPPED", {"reason": "recent_refresh"})
    now = datetime.now(timezone.utc)
    conn.execute("UPDATE v2c_operation_runs SET started_at=? WHERE run_id=?",
                 ((now - timedelta(hours=21)).isoformat(timespec="seconds"), rid_ok))
    conn.execute("UPDATE v2c_operation_runs SET started_at=? WHERE run_id=?",
                 ((now - timedelta(minutes=30)).isoformat(timespec="seconds"), rid_skip))
    conn.commit()
    monkeypatch.setattr(live_prices, "refresh_live_prices", lambda conn, **kw: {"status": "OK"})
    assert maybe_refresh_live_prices(conn)["status"] == "OK"


def test_price_refresh_skip_chain_respects_recent_success(tmp_path, monkeypatch):
    """A SKIPPED row after a fresh OK must not weaken the gate either."""
    monkeypatch.delenv("MOZES_PRICE_REFRESH", raising=False)
    conn = _conn(tmp_path)
    rid_ok = operation_start(conn, "prices")
    operation_finish(conn, rid_ok, "OK", {})
    rid_skip = operation_start(conn, "prices")
    operation_finish(conn, rid_skip, "SKIPPED", {"reason": "recent_refresh"})
    now = datetime.now(timezone.utc)
    conn.execute("UPDATE v2c_operation_runs SET started_at=? WHERE run_id=?",
                 ((now - timedelta(hours=2)).isoformat(timespec="seconds"), rid_ok))
    conn.execute("UPDATE v2c_operation_runs SET started_at=? WHERE run_id=?",
                 ((now - timedelta(minutes=30)).isoformat(timespec="seconds"), rid_skip))
    conn.commit()
    monkeypatch.setattr(live_prices, "refresh_live_prices",
                        lambda conn, **kw: (_ for _ in ()).throw(AssertionError("must not run")))
    out = maybe_refresh_live_prices(conn)
    assert out["status"] == "SKIPPED" and out["reason"] == "recent_refresh"
    assert out["last_status"] == "OK"


def test_price_refresh_retries_systemic_failure_sooner(tmp_path, monkeypatch):
    monkeypatch.delenv("MOZES_PRICE_REFRESH", raising=False)
    conn = _conn(tmp_path)
    _record_prices_op(conn, "PARTIAL", 4)
    monkeypatch.setattr(live_prices, "refresh_live_prices", lambda conn, **kw: {"status": "OK"})
    assert maybe_refresh_live_prices(conn)["status"] == "OK"


def test_tiingo_fallback_when_yahoo_blocked(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db.upsert_watch(conn, "AAA", "Alpha")
    monkeypatch.setattr(live_prices, "TIINGO_API_KEY", "test-key")
    monkeypatch.setattr(live_prices, "fetch_tiingo",
                        lambda ticker, start, end: [{"date": "2026-09-30", "close": 42, "volume": 100}])

    def blocked(ticker, start, end):
        raise RuntimeError("HTTP 429")

    out = refresh_live_prices(conn, today=date(2026, 10, 1), fetcher=blocked)
    assert out["status"] == "OK" and out["updated"] == 2
    assert db.load_prices(conn, "AAA")[-1]["close"] == 42
    assert db.load_prices(conn, "AAA")[-1]["source"] == "tiingo-live"


def test_budget_defers_remaining_tickers(tmp_path):
    conn = _conn(tmp_path)
    db.upsert_watch(conn, "AAA", "Alpha")
    out = refresh_live_prices(conn, today=date(2026, 10, 1), budget_seconds=0)
    assert out["status"] == "BOUNDED" and out["bounded"] is True
    assert set(out["deferred_tickers"]) == {"AAA", "XBI"}
