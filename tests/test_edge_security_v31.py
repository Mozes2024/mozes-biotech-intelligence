from mozes import db
from mozes.alert_dispatch import verified_issuer_identity, build_stage1_payload
from mozes.entity_resolution import update_company_map
from mozes.live_monitor import record_change


def test_listing_type_not_ticker_suffix_determines_verified_equity(tmp_path):
    conn = db.connect(tmp_path / "equity.db")
    rows = [{"ticker": t, "cik": str(i + 100), "name": f"Issuer {t}", "exchange": "NASDAQ"}
            for i, t in enumerate(["BBW", "DOYU", "BMR", "XYZW", "XYZU", "XYZR"])]
    listings = [{**r, "name": r["name"] + (" Common Stock" if i < 3 else [" Warrants", " Units", " Rights"][i - 3]),
                 "source_url": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"}
                for i, r in enumerate(rows)]
    for r in rows:
        db.upsert_security_lifecycle(conn, r["ticker"], status="ACTIVE", source_url=listings[0]["source_url"])
    update_company_map(conn, rows, listings)
    for ticker in ["BBW", "DOYU", "BMR"]:
        assert verified_issuer_identity(conn, ticker), ticker
        change = record_change(conn, ticker=ticker, change_type="wire_release_signal", previous_value=None,
            new_value={"headline": "Phase 2 trial met its primary endpoint"}, source_type="wire",
            source_url="https://www.businesswire.com/example/" + ticker, severity="high")
        assert build_stage1_payload(conn, change)["priority"] == "P1"
    for ticker in ["XYZW", "XYZU", "XYZR"]:
        # Even a stale projection cannot override authoritative derivative evidence.
        row = next(row for row in rows if row["ticker"] == ticker)
        conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                     (ticker.lower(), row["name"], ticker, row["cik"], .99, "SEC-v2C-equity", "2026-10-08"))
        assert verified_issuer_identity(conn, ticker) is None
