"""Price ingestion. No free, licensable, high-quality EOD feed is bundled.
Options: user-supplied CSV (date,close[,volume]) or Tiingo (free tier, API key)."""
import csv
import json
import urllib.request
from datetime import datetime, timezone

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


def fetch_yahoo_chart(ticker, start, end):
    """Keyless best-effort adapter; callers should retain the provider provenance."""
    start_ts = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start_ts}&period2={end_ts}&interval=1d&events=history"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MOZES research"})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.loads(response.read().decode())["chart"]["result"][0]
    quote = data["indicators"]["quote"][0]
    adjusted = data["indicators"].get("adjclose", [{}])[0].get("adjclose", [])
    rows = []
    for i, stamp in enumerate(data.get("timestamp", [])):
        close = (adjusted[i] if i < len(adjusted) else None) or quote["close"][i]
        if close is not None:
            rows.append({"date": datetime.fromtimestamp(stamp, timezone.utc).date().isoformat(), "close": close,
                         "volume": quote.get("volume", [None] * len(data["timestamp"]))[i]})
    return rows


def store(conn, ticker, rows, source):
    with conn:
        conn.executemany("INSERT OR REPLACE INTO prices (ticker, date, close, volume, source) VALUES (?,?,?,?,?)",
                         [(ticker, r["date"], r["close"], r.get("volume"), source) for r in rows])
