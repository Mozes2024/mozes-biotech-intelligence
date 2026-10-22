"""Persisted watch_universe rows for delisted/renamed tickers stop being monitored, history stays."""
from mozes import db
from mozes.hot_monitor import seed_priority_watches
from mozes.ingest.edgar_latest import biotech_universe
from mozes.live_monitor import record_change
from mozes.watch_reconcile import reconcile_retired_watches, retired_tickers


def _legacy_db(tmp_path):
    conn = db.connect(tmp_path / "legacy.db")
    for ticker, cik in (("BPMC", "1597264"), ("BGNE", "1651308"), ("AMGN", "318154"), ("ACLX", "9999999")):
        conn.execute("INSERT INTO watch_universe(ticker,company,cik,source,active,updated_at) VALUES(?,?,?,?,1,'2026-10-01')",
                     (ticker, ticker + " Inc", cik, "priority_hot_lane"))
    conn.commit()
    return conn


def test_retired_list_contains_bpmc_and_bgne_with_successor():
    retired = retired_tickers()
    assert retired["BPMC"]["successor"] is None
    assert retired["BGNE"]["successor"] == "ONC"
    assert retired["ACLX"]["successor"] is None


def test_reconcile_deactivates_retired_rows_but_keeps_them_and_history(tmp_path):
    conn = _legacy_db(tmp_path)
    record_change(conn, ticker="BPMC", change_type="wire_release_signal", previous_value=None,
                  new_value={"headline": "old"}, source_url="https://example.com/a", source_type="wire",
                  identity=["wire", "https://example.com/a"])
    before_ids = [r[0] for r in conn.execute("SELECT change_id FROM change_events")]
    result = reconcile_retired_watches(conn)
    assert sorted(result["deactivated"]) == ["ACLX", "BGNE", "BPMC"]
    rows = {r["ticker"]: r for r in conn.execute("SELECT * FROM watch_universe")}
    assert rows["BPMC"]["active"] == 0 and rows["BGNE"]["active"] == 0 and rows["AMGN"]["active"] == 1
    assert rows["ACLX"]["active"] == 0
    assert len(rows) == 5 or len(rows) == 4  # rows are retained (ONC may be added with the carried CIK)
    assert [r[0] for r in conn.execute("SELECT change_id FROM change_events")] == before_ids
    assert rows["BPMC"]["cik"] == "1597264"  # data untouched
    # idempotent
    assert reconcile_retired_watches(conn)["deactivated"] == []


def test_successor_inherits_cik_so_sec_polling_continues(tmp_path):
    conn = _legacy_db(tmp_path)
    reconcile_retired_watches(conn)
    onc = conn.execute("SELECT cik,active FROM watch_universe WHERE ticker='ONC'").fetchone()
    assert onc["cik"] == "1651308" and onc["active"] == 1
    seed_priority_watches(conn)
    assert conn.execute("SELECT active FROM watch_universe WHERE ticker='ONC'").fetchone()[0] == 1
    assert conn.execute("SELECT cik FROM watch_universe WHERE ticker='ONC'").fetchone()[0] == "1651308"


def test_seed_pass_reconciles_and_discovery_cannot_reactivate(tmp_path):
    conn = _legacy_db(tmp_path)
    out = seed_priority_watches(conn)
    assert sorted(out["retired"]["deactivated"]) == ["ACLX", "BGNE", "BPMC"]
    db.upsert_watch(conn, "BPMC", company="Blueprint", source="dynamic_news_discovery")
    assert conn.execute("SELECT active FROM watch_universe WHERE ticker='BPMC'").fetchone()[0] == 0
    active = {r["ticker"] for r in db.watch_rows(conn)}
    assert "BPMC" not in active and "BGNE" not in active and "AMGN" in active


def test_sec_universe_excludes_retired_even_with_active_trials(tmp_path):
    conn = _legacy_db(tmp_path)
    reconcile_retired_watches(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sponsor_ticker_map)")]
    assert "ticker" in cols
    ciks = {item["ticker"] for item in biotech_universe(conn).values()}
    assert "BPMC" not in ciks and "BGNE" not in ciks
