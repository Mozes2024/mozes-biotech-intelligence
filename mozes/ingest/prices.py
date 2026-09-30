"""Price ingestion. No free, licensable, high-quality EOD feed is bundled.
Options: user-supplied CSV (date,close[,volume]) or Tiingo (free tier, API key)."""
import csv
import json
import urllib.request

from ..config import TIINGO_API_KEY


def load_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [{"date": r["date"][:10], "close": float(r.get("adj_close") or r["close"]),
                 "volume": float(r["volume"]) if r.get("volume") else None} for r in csv.DictReader(fh)]


def fetch_tiingo(ticker, start, end):
    if not TIINGO_API_KEY:
        raise RuntimeError("TIINGO_API_KEY not set")
    url = (f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?startDate={start}&endDate={end}"
           f"&token={TIINGO_API_KEY}")
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.loads(r.read().decode())
    return [{"date": d["date"][:10], "close": d["adjClose"], "volume": d.get("adjVolume")} for d in data]


def store(conn, ticker, rows, source):
    with conn:
        conn.executemany("INSERT OR REPLACE INTO prices (ticker, date, close, volume, source) VALUES (?,?,?,?,?)",
                         [(ticker, r["date"], r["close"], r.get("volume"), source) for r in rows])
