"""Keyless Nasdaq Trader current listing directories (not an action history)."""
from __future__ import annotations

import csv
import io
import time
from ..source_observability import record
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


def fetch_directory(source_url: str, *, deadline=None) -> list[dict]:
    request = Request(source_url, headers={"User-Agent": "MOZES Biotech Intelligence listing audit"})
    started = time.monotonic()
    try:
        remaining = 30 if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('listing audit deep deadline exhausted')
        with urlopen(request, timeout=min(30, remaining)) as response:
            content = response.read().decode("utf-8-sig")
        rows = parse_directory(content, source_url)
        if not rows:
            raise ValueError('empty listing directory')
    except Exception:
        record('nasdaq', error=True, duration_ms=(time.monotonic() - started) * 1000)
        raise
    record('nasdaq', duration_ms=(time.monotonic() - started) * 1000)
    return rows


def fetch_current_listings(*, deadline=None) -> list[dict]:
    # Both files must load: a partial universe would create false UNKNOWN states.
    return fetch_directory(NASDAQ_URL, deadline=deadline) + fetch_directory(OTHER_URL, deadline=deadline)
