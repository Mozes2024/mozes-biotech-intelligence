"""Synthetic fixtures for unit tests ONLY. Never used for reported results."""
from datetime import date, timedelta


def make_price_event(eid, date_, growth, n=130, bench_growth=0.0, type_="P3_TOPLINE"):
    d0 = date.fromisoformat(date_)
    prices = [{"date": (d0 - timedelta(days=n - k)).isoformat(), "close": 100 * (1 + growth) ** k} for k in range(n)]
    bench = [{"date": p["date"], "close": 100 * (1 + bench_growth) ** k} for k, p in enumerate(prices)]
    return {"id": eid, "ticker": eid, "type": type_, "date": date_, "prices": prices, "bench": bench,
            "features": {}, "features_as_of": None}
