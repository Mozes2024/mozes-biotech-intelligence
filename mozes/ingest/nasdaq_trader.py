"""Keyless Nasdaq Trader current listing directories (not an action history)."""
from __future__ import annotations

import csv
import io
from urllib.request import Request, urlopen

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def parse_directory(content: str, source_url: str) -> list[dict]:
    rows = []
    for row in csv.DictReader(io.StringIO(content), delimiter="|"):
        ticker = (row.get("Symbol") or row.get("ACT Symbol") or "").strip().upper()
        if not ticker or ticker.startswith("FILE CREATION TIME"):
            continue
        rows.append({
            "ticker": ticker,
            "name": (row.get("Security Name") or "").strip(),
            "exchange": "NASDAQ" if "nasdaqlisted" in source_url else (row.get("Exchange") or "").strip(),
            "test_issue": (row.get("Test Issue") or "").strip(),
            "financial_status": (row.get("Financial Status") or "").strip() or None,
            "etf": (row.get("ETF") or "").strip(),
            "source_url": source_url,
        })
    return rows


def fetch_directory(source_url: str) -> list[dict]:
    request = Request(source_url, headers={"User-Agent": "MOZES Biotech Intelligence listing audit"})
    with urlopen(request, timeout=30) as response:
        content = response.read().decode("utf-8-sig")
    return parse_directory(content, source_url)


def fetch_current_listings() -> list[dict]:
    # Both files must load: a partial universe would create false UNKNOWN states.
    return fetch_directory(NASDAQ_URL) + fetch_directory(OTHER_URL)
