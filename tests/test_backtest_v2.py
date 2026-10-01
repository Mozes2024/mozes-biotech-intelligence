from pathlib import Path
from datetime import date, timedelta

from mozes import db
from mozes.backtest_v2 import all_runup_grids, report, runup_one
from mozes.historical import attach_price_coverage, backfill_prices, import_bundle
from mozes.ingest.prices import load_csv
from mozes.radar import bootstrap_database


def test_all_grid_is_reported_not_only_best():
    rows=[{"stock":[{"date":f"2026-01-{i:02d}","close":100+i} for i in range(1,32)],"benchmark":[]}]
    g=all_runup_grids(rows)
    assert len(g) > 10
    assert {(x["entry"],x["exit"]) for x in g}


def test_runup_benchmark_alignment_can_be_missing_without_wrong_row_alignment():
    s=[{"date":f"2026-01-{i:02d}","close":100+i} for i in range(1,32)]
    b=[{"date":f"2026-02-{i:02d}","close":100+i} for i in range(1,20)]
    r=runup_one(s,b,14,1)
    assert r["excess"] is None


def _starter(conn):
    root = Path(__file__).resolve().parents[1] / "mozes" / "data"
    bootstrap_database(conn)
    import_bundle(conn, root / "historical_starter.json")
    return root / "starter_prices"


def test_clean_kod_prices_cannot_backtest_legacy_kod_case(tmp_path):
    conn = db.connect(tmp_path / "backtest.db")
    prices = _starter(conn)
    backfill_prices(conn, "PIT-KOD-DAYBREAK-20260928", "csv",
                    stock_file=str(prices / "KOD.csv"), benchmark_file=str(prices / "XBI.csv"))

    result = report(conn)
    assert result["coverage"]["historical_cases_total"] == 24
    assert result["coverage"]["legacy_quarantined"] == 21
    assert result["coverage"]["runup_ready_cases"] == 1
    assert result["coverage"]["hold_ready_cases"] == 0
    assert result["coverage"]["runup_case_ids"] == ["PIT-KOD-DAYBREAK-20260928"]
    assert "KOD-2026-09" not in result["coverage"]["runup_case_ids"]
    assert result["hold_through"]["stats"]["n"] == 0
    assert result["hold_through"]["rows"] == []
    assert all(grid["return"]["n"] <= 1 for grid in result["runup_all_grids"])
    assert any(grid["return"]["n"] == 1 for grid in result["runup_all_grids"])


def test_ticker_prices_without_case_attachment_do_not_enter_backtest(tmp_path):
    conn = db.connect(tmp_path / "backtest.db")
    prices = _starter(conn)
    db.store_prices(conn, "KOD", load_csv(prices / "KOD.csv"), "csv")
    db.store_prices(conn, "XBI", load_csv(prices / "XBI.csv"), "csv")

    result = report(conn)
    assert result["coverage"]["runup_ready_cases"] == 0
    assert result["hold_through"]["stats"]["n"] == 0
    assert all(grid["return"]["n"] == 0 for grid in result["runup_all_grids"])


def test_later_ticker_price_ingestion_cannot_change_attached_case_return(tmp_path):
    conn = db.connect(tmp_path / "backtest.db")
    prices = _starter(conn)
    backfill_prices(conn, "PIT-KOD-DAYBREAK-20260928", "csv",
                    stock_file=str(prices / "KOD.csv"), benchmark_file=str(prices / "XBI.csv"))
    before = next(grid for grid in report(conn)["runup_all_grids"]
                  if grid["entry"] == 60 and grid["exit"] == 1)["return"]
    replacement = [{"date": row["date"], "close": 1000 - index}
                   for index, row in enumerate(load_csv(prices / "KOD.csv"))]
    db.store_prices(conn, "KOD", replacement, "csv")
    after = next(grid for grid in report(conn)["runup_all_grids"]
                 if grid["entry"] == 60 and grid["exit"] == 1)["return"]
    assert before == after


def test_only_explicit_hold_ready_case_enters_hold_statistics(tmp_path):
    conn = db.connect(tmp_path / "backtest.db")
    bootstrap_database(conn)
    db.archive_source(conn, "pre", "https://example.test/pre", "company_ir", "2025-01-01T00:00:00Z",
                      "2025-01-02T00:00:00Z", "pre-event fact")
    db.archive_source(conn, "post", "https://example.test/post", "company_ir", "2025-01-10T14:00:00Z",
                      "2025-01-11T00:00:00Z", "outcome fact")
    db.upsert_historical_case(conn, "PIT-HOLD", "TEST", "P3_TOPLINE", "2025-01-10T13:00:00Z",
                              announcement_session="premarket", provenance=["pre", "post"])
    db.store_feature_snapshot(conn, "PIT-HOLD-S", "PIT-HOLD", "2025-01-09T12:00:00Z",
                              {"known": True}, provenance=["pre"], blinded=True)
    db.store_outcome_label(conn, "PIT-HOLD-O", "PIT-HOLD", "2025-01-11T00:00:00Z",
                           {"clinical": "success", "event_return": 0.2}, provenance=["post"], verified=True)
    rows = [{"date": (date(2024, 8, 1) + timedelta(days=day)).isoformat(), "close": 10 + day}
            for day in range(195)]
    for ticker in ("TEST", "XBI"):
        db.store_prices(conn, ticker, rows, "csv")
    attach_price_coverage(conn, "PIT-HOLD", "csv")

    result = report(conn)
    assert result["coverage"]["hold_ready_cases"] == 1
    assert result["hold_through"]["stats"]["n"] == 1
    assert [row["id"] for row in result["hold_through"]["rows"]] == ["PIT-HOLD"]
