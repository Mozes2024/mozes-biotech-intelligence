from datetime import date, timedelta

from mozes import db
from mozes.live_intelligence import enrich_live_rows, market_intelligence_for_row, recent_completed_catalyst


def _prices(start, closes, volumes=None):
    volumes = volumes or [100] * len(closes)
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "close": close, "volume": volumes[i]}
        for i, close in enumerate(closes)
    ]


def test_market_attention_flags_post_event_chase_without_changing_recommendation(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    start = date(2026, 8, 1)
    stock = _prices(start, [10 + i * 0.05 for i in range(30)] + [35], [100] * 30 + [600])
    xbi = _prices(start, [100 + i * 0.05 for i in range(31)], [1000] * 31)
    db.store_prices(conn, "KOD", stock, "csv")
    db.store_prices(conn, "XBI", xbi, "csv")
    row = {
        "ticker": "KOD",
        "recommendation": {"status": "RESEARCH_WORTHY"},
        "market": {"available": True, "returns": {"T-7": 1.2, "T-30": 1.5},
                   "relative_to_xbi": {"T-7": 1.15, "T-30": 1.45}},
    }
    before = row["recommendation"].copy()
    out = market_intelligence_for_row(conn, row, "2026-10-01")
    assert out["available"]
    assert out["post_event_chase_risk"]
    assert out["relative_volume_20d"] >= 5
    assert out["context"] == "stock_specific"
    assert row["recommendation"] == before


def test_recent_clean_case_explains_same_ticker_future_catalyst(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.upsert_historical_case(conn, "PIT-KOD-DAYBREAK", "KOD", "P3_TOPLINE", "2026-09-28T10:30:00Z",
                              announcement_session="premarket", provenance=["issuer"])
    with conn:
        conn.executemany(
            "INSERT INTO historical_case_prices(case_id,ticker,date,close,volume,source) VALUES(?,?,?,?,?,?)",
            [
                ("PIT-KOD-DAYBREAK", "KOD", "2026-09-25", 32.35, 100, "csv"),
                ("PIT-KOD-DAYBREAK", "KOD", "2026-09-28", 90.0, 1000, "csv"),
                ("PIT-KOD-DAYBREAK", "KOD", "2026-09-30", 94.8, 800, "csv"),
            ],
        )
    r = recent_completed_catalyst(conn, "KOD", date(2026, 10, 1))
    assert r["catalyst_type"] == "P3_TOPLINE"
    assert r["days_since"] == 3
    assert r["return_since_event"] > 1.9


def test_enrichment_builds_same_ticker_chain_and_keeps_core_status(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    rows = [
        {"id": "KOD-BLA", "ticker": "KOD", "type": "FILING", "program": "Zenkuda BLA",
         "date": {"window": {"start": "2026-10-01", "end": "2026-12-31"}}, "days_to": 0,
         "recommendation": {"status": "WATCH"}, "state": {"verification_state": "VERIFIED"},
         "market": {"available": False, "returns": {}, "relative_to_xbi": {}}},
        {"id": "KOD-PEAK", "ticker": "KOD", "type": "P3_TOPLINE", "program": "PEAK",
         "date": {"window": {"start": "2026-12-01", "end": "2026-12-31"}}, "days_to": 61,
         "recommendation": {"status": "RESEARCH_WORTHY"}, "state": {"verification_state": "VERIFIED"},
         "market": {"available": False, "returns": {}, "relative_to_xbi": {}}},
    ]
    enrich_live_rows(conn, rows, date(2026, 10, 1))
    assert len(rows[0]["catalyst_chain"]) == 2
    assert rows[0]["catalyst_chain"][1]["id"] == "KOD-PEAK"
    assert rows[0]["recommendation"]["status"] == "WATCH"
    assert rows[1]["recommendation"]["status"] == "RESEARCH_WORTHY"
