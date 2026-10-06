"""Official IR / RSS registry for the hot monitoring cohort."""
from __future__ import annotations

import json
import re
from pathlib import Path

FILE = Path(__file__).parent / "data" / "official_ir_sites.json"
_TICKER = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


def _normalize_entry(ticker, value):
    ticker = str(ticker or "").strip().upper()
    if not _TICKER.fullmatch(ticker):
        return None
    if isinstance(value, str):
        site = value.strip()
        return {"ticker": ticker, "site": site, "feed_url": None, "company": ticker} if site else None
    if not isinstance(value, dict):
        return None
    site = (value.get("site") or value.get("url") or "").strip()
    feed = (value.get("feed_url") or value.get("rss") or "").strip() or None
    company = (value.get("company") or ticker).strip()
    if not site:
        return None
    return {"ticker": ticker, "site": site, "feed_url": feed, "company": company}


def load_ir_registry():
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for ticker, value in raw.items():
        row = _normalize_entry(ticker, value)
        if row:
            out[row["ticker"]] = row
    return out


def ir_site(ticker):
    row = load_ir_registry().get(str(ticker or "").upper())
    return row["site"] if row else None


def ir_feed(ticker):
    row = load_ir_registry().get(str(ticker or "").upper())
    return row.get("feed_url") if row else None


def ir_entries():
    return list(load_ir_registry().values())
