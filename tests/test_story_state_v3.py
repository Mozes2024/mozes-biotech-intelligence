from mozes import db
from mozes.alert_dispatch import build_stage1_payload, verified_issuer_identity
from mozes.live_monitor import record_change


def _change(conn, headline, source, suffix):
    return record_change(
        conn, ticker="ZZZZ", change_type="wire_release_signal", previous_value=None,
        new_value={"headline": headline}, source_url=f"https://{source}.example/{suffix}",
        source_type="wire", severity="high", identity=[source, suffix],
    )


def test_same_positive_outcome_is_corrobated(tmp_path):
    conn = db.connect(tmp_path / "same.db")
    first = _change(conn, "ZZZZ announces positive Phase 2 topline results for ALPHA", "businesswire", "1")
    second = _change(conn, "ZZZZ reports positive Phase 2 topline results for ALPHA", "globenewswire", "2")
    assert conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0] == 1
    assert conn.execute("SELECT primary_change_id FROM alert_links WHERE change_id=?", (second,)).fetchone()[0] == first


def test_material_state_changes_are_fresh_alerts(tmp_path):
    pairs = [
        ("ZZZZ Phase 3 ALPHA results announced", "ZZZZ Phase 3 ALPHA results did not meet primary endpoint"),
        ("ZZZZ FDA decision review of ALPHA continues", "ZZZZ FDA decision review of ALPHA receives complete response letter"),
        ("ZZZZ ALPHA placed on clinical hold", "ZZZZ ALPHA clinical hold lifted"),
        ("ZZZZ ALPHA positive Phase 3 results", "ZZZZ ALPHA negative Phase 3 results did not meet primary endpoint"),
    ]
    for i, (first, second) in enumerate(pairs):
        conn = db.connect(tmp_path / f"state-{i}.db")
        _change(conn, first, "businesswire", "1")
        _change(conn, second, "globenewswire", "2")
        assert conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0] == 2, i
        assert conn.execute("SELECT COUNT(*) FROM alert_links").fetchone()[0] == 0


def test_verified_dynamic_issuer_requires_unique_active_equity(tmp_path):
    conn = db.connect(tmp_path / "identity.db")
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("novel", "Novel Bio", "ZZZZ", "123", .99, "SEC-v2C-equity", "2026-10-08"))
    db.upsert_security_lifecycle(conn, "ZZZZ", status="ACTIVE", source_url="https://www.sec.gov/files/company_tickers_exchange.json")
    assert verified_issuer_identity(conn, "ZZZZ")["cik"] == "123"
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("collision", "Other Bio", "ZZZZ", "456", .99, "SEC-v2C-equity", "2026-10-08"))
    assert verified_issuer_identity(conn, "ZZZZ") is None
    assert verified_issuer_identity(conn, "ZZZZ.W") is None


def test_dynamic_issuer_p1_only_with_verified_identity(tmp_path):
    conn = db.connect(tmp_path / "priority.db")
    headline = "ZZZZ Phase 2 trial met its primary endpoint"
    change = _change(conn, headline, "businesswire", "1")
    assert build_stage1_payload(conn, change)["priority"] == "P2"
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("novel", "Novel Bio", "ZZZZ", "123", .99, "SEC-v2C-equity", "2026-10-08"))
    db.upsert_security_lifecycle(conn, "ZZZZ", status="ACTIVE", source_url="https://www.sec.gov/files/company_tickers_exchange.json")
    payload = build_stage1_payload(conn, change)
    assert payload["priority"] == "P1" and payload["verified_issuer_cik"] == "123"
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("collision", "Other Bio", "ZZZZ", "456", .99, "SEC-v2C-equity", "2026-10-08"))
    assert build_stage1_payload(conn, change)["priority"] == "P2"
