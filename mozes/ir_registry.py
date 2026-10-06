"""Official IR / RSS registry for the hot monitoring cohort."""
from __future__ import annotations

import json
import re
from pathlib import Path

FILE = Path(__file__).parent / "data" / "official_ir_sites.json"
PRODUCTS_FILE = Path(__file__).parent / "data" / "product_aliases.json"
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


def load_aliases():
    """Company names (registry) and product names (product_aliases.json) per ticker."""
    aliases = {}
    for row in ir_entries():
        names = [part.strip() for part in re.split(r"\s*/\s*", row.get("company") or "")]
        aliases.setdefault(row["ticker"], []).extend(
            ("company", name) for name in names if len(name) >= 5)
    try:
        products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        products = {}
    for ticker, names in (products or {}).items():
        ticker = str(ticker).upper()
        if _TICKER.fullmatch(ticker) and isinstance(names, list):
            aliases.setdefault(ticker, []).extend(
                ("product", str(name).strip()) for name in names if len(str(name).strip()) >= 3)
    return aliases


def resolve_alias(text, aliases=None):
    """Unique ticker whose company/product name appears in text, else None (ambiguity is not a match)."""
    text = text or ""
    matches = {}
    for ticker, names in (aliases if aliases is not None else load_aliases()).items():
        for kind, name in names:
            pattern = r"(?<![\w-])" + re.escape(name).replace(r"\ ", r"[\s-]+") + r"(?![\w-])"
            if re.search(pattern, text, re.I):
                matches.setdefault(ticker, (kind, name))
                break
    if len(matches) != 1:
        return None
    ticker, (kind, name) = next(iter(matches.items()))
    return {"ticker": ticker, "source": f"alias_{kind}", "matched": name}
