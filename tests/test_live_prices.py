from datetime import date

from mozes import db
from mozes.live_prices import refresh_live_prices


def test_live_price_refresh_updates_watchlist_and_xbi(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.upsert_watch(conn, "AAA", "Alpha")
    db.upsert_watch(conn, "BBB", "Beta")

    def fake_fetch(ticker, start, end):
        base = 100 if ticker == "XBI" else 10 if ticker == "AAA" else 20
        return [
            {"date": "2026-09-29", "close": base, "volume": 100},
            {"date": "2026-09-30", "close": base + 1, "volume": 150},
        ]

    out = refresh_live_prices(conn, today=date(2026, 10, 1), fetcher=fake_fetch)
    assert out["updated"] == 3
    assert set(out["tickers"]) == {"AAA", "BBB", "XBI"}
    assert db.load_prices(conn, "AAA")[-1]["date"] == "2026-09-30"
    assert db.load_prices(conn, "XBI")[-1]["close"] == 101


def test_live_price_refresh_is_best_effort(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.upsert_watch(conn, "AAA", "Alpha")

    def flaky(ticker, start, end):
        if ticker == "AAA":
            raise RuntimeError("temporary provider error")
        return [{"date": "2026-09-30", "close": 100, "volume": 1000}]

    out = refresh_live_prices(conn, today=date(2026, 10, 1), fetcher=flaky)
    assert out["updated"] == 1
    assert out["errors"][0]["ticker"] == "AAA"
    assert db.load_prices(conn, "XBI")
