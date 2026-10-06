"""Primary / near-primary hot feeds: FDA RSS, wire services, Nasdaq trade halts.

Secondary aggregators remain discovery backups; these feeds feed Stage-1 alerts.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .live_monitor import observe, record_change
from .materiality import classify_outcome

FDA_FEEDS = (
    ("fda_press", "https://www.fda.gov/about-fda/contact-fda/subscribe-podcasts-and-news-feeds"),
    # Direct RSS endpoints published by FDA "Subscribe to Podcasts and News Feeds".
    ("fda_press_releases", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"),
    ("fda_drugs", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml"),
    ("fda_biologics", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/blood-biologics/rss.xml"),
)

WIRE_FEEDS = (
    ("businesswire_biotech", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFpRWA=="),
    ("globenewswire_biotech", "https://www.globenewswire.com/RssFeed/industry/Biotechnology/subject/Biotechnology"),
    ("prnewswire_health", "https://www.prnewswire.com/rss/health-latest-news.rss"),
)

NASDAQ_HALTS_FEED = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"

BIOTECHISH = re.compile(
    r"\b(biotech|therapeutics?|pharma(?:ceutical)?s?|oncology|clinical\s+trial|"
    r"phase\s*[123]|fda|pdufa|topline|endpoint|antibody|gene\s+therapy)\b",
    re.I,
)
HALT_CODES = re.compile(r"\b(T1|T2|LUDP|M)\b", re.I)


def _public_https(url):
    parts = urlsplit(url or "")
    return parts.scheme == "https" and bool(parts.hostname)


def _parse_rss_items(payload, *, now=None, max_age_hours=24):
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(payload)
    items = []
    channel_items = root.findall("./channel/item")
    if not channel_items and root.tag.endswith("feed"):
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.findall(ns + "entry"):
            title = (entry.findtext(ns + "title") or "").strip()
            link = next((node.get("href") for node in entry.findall(ns + "link")
                         if node.get("rel", "alternate") == "alternate"), "") or ""
            stamp = entry.findtext(ns + "published") or entry.findtext(ns + "updated") or ""
            try:
                published = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                continue
            if title and link and now - timedelta(hours=max_age_hours) <= published <= now + timedelta(minutes=15):
                items.append({"title": title, "url": link, "published_at": published.isoformat(),
                              "summary": (entry.findtext(ns + "summary") or "")[:500]})
        return items
    for item in channel_items:
        title = "".join((item.find("title").itertext() if item.find("title") is not None else [])).strip()
        link = (item.findtext("link") or "").strip()
        summary = "".join((item.find("description").itertext() if item.find("description") is not None else []))[:500]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError):
            continue
        if not title or not link:
            continue
        if not now - timedelta(hours=max_age_hours) <= published <= now + timedelta(minutes=15):
            continue
        items.append({"title": title, "url": link, "published_at": published.isoformat(), "summary": summary})
    return items


def poll_fda_feeds(conn, *, fetch, now=None, max_items=20):
    now = now or datetime.now(timezone.utc)
    seen = errors = 0
    for name, url in FDA_FEEDS:
        if "subscribe-podcasts" in url:
            continue
        try:
            items = _parse_rss_items(fetch(url), now=now, max_age_hours=36)[:max_items]
            observe(conn, f"fda_feed:{name}", {"status": "active", "checked_at": now.isoformat(), "items": len(items)},
                    source_url=url, source_type="fda")
            for item in items:
                text = item["title"] + " " + item.get("summary", "")
                if not BIOTECHISH.search(text):
                    continue
                outcome = classify_outcome(text)
                before = conn.total_changes
                record_change(
                    conn, ticker=None, change_type="fda_release_signal", previous_value=None,
                    new_value={"headline": item["title"], "published_at": item["published_at"],
                               "summary": item.get("summary", ""), "outcome": outcome},
                    source_url=item["url"], source_type="fda",
                    severity="high" if outcome.get("material") else "medium",
                    verification_state="investigation_only",
                    identity=["fda", name, item["url"]],
                    metadata={"feed": name, "source_published_at": item["published_at"]},
                )
                seen += conn.total_changes > before
        except (OSError, ValueError, ET.ParseError, TypeError) as exc:
            errors += 1
            observe(conn, f"fda_feed:{name}", {"status": "fetch_error", "error": str(exc)[:160],
                                               "checked_at": now.isoformat()},
                    source_url=url, source_type="fda")
    return {"signals_seen": seen, "errors": errors}


def poll_wire_feeds(conn, *, fetch, now=None, resolve_headline=None, max_items=30):
    """Wire headlines are investigation signals; SEC/FDA remain verification."""
    now = now or datetime.now(timezone.utc)
    from .news_signals import resolve_headline as default_resolve
    resolve_headline = resolve_headline or default_resolve
    seen = errors = 0
    for name, url in WIRE_FEEDS:
        try:
            items = _parse_rss_items(fetch(url), now=now, max_age_hours=12)[:max_items]
            observe(conn, f"wire_feed:{name}", {"status": "active", "checked_at": now.isoformat(), "items": len(items)},
                    source_url=url, source_type="wire")
            for item in items:
                text = item["title"] + " " + item.get("summary", "")
                if not BIOTECHISH.search(text):
                    continue
                issuer = resolve_headline(conn, item["title"])
                ticker = issuer["ticker"] if issuer else None
                outcome = classify_outcome(text)
                before = conn.total_changes
                record_change(
                    conn, ticker=ticker, change_type="wire_release_signal", previous_value=None,
                    new_value={"headline": item["title"], "published_at": item["published_at"],
                               "summary": item.get("summary", ""), "outcome": outcome},
                    source_url=item["url"], source_type="wire",
                    severity="high" if ticker and outcome.get("material") else "medium",
                    verification_state="investigation_only",
                    identity=["wire", name, item["url"]],
                    metadata={"feed": name, "identity_source": (issuer or {}).get("source"),
                              "source_published_at": item["published_at"]},
                )
                seen += conn.total_changes > before
        except (OSError, ValueError, ET.ParseError, TypeError) as exc:
            errors += 1
            observe(conn, f"wire_feed:{name}", {"status": "fetch_error", "error": str(exc)[:160],
                                                "checked_at": now.isoformat()},
                    source_url=url, source_type="wire")
    return {"signals_seen": seen, "errors": errors}


def _halt_ticker(text):
    match = re.search(r"\b([A-Z][A-Z0-9.]{0,9})\b", text or "")
    return match.group(1) if match else None


def poll_nasdaq_halts(conn, *, fetch, now=None, watch_tickers=None):
    """T1 / news-pending halts are among the earliest public catalyst signals."""
    now = now or datetime.now(timezone.utc)
    watch = {t.upper() for t in (watch_tickers or [])}
    try:
        raw = fetch(NASDAQ_HALTS_FEED)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        items = _parse_rss_items(raw, now=now, max_age_hours=6)
    except (OSError, ValueError, ET.ParseError, UnicodeError) as exc:
        observe(conn, "nasdaq_halts", {"status": "fetch_error", "error": str(exc)[:160],
                                       "checked_at": now.isoformat()},
                source_url=NASDAQ_HALTS_FEED, source_type="nasdaq_halts")
        return {"signals_seen": 0, "errors": 1}
    observe(conn, "nasdaq_halts", {"status": "active", "checked_at": now.isoformat(), "items": len(items)},
            source_url=NASDAQ_HALTS_FEED, source_type="nasdaq_halts")
    seen = 0
    for item in items:
        text = item["title"] + " " + item.get("summary", "")
        if not HALT_CODES.search(text):
            continue
        ticker = _halt_ticker(item["title"]) or _halt_ticker(item.get("summary", ""))
        if watch and ticker and ticker not in watch:
            continue
        before = conn.total_changes
        record_change(
            conn, ticker=ticker, change_type="nasdaq_halt_signal", previous_value=None,
            new_value={"headline": item["title"], "published_at": item["published_at"],
                       "summary": item.get("summary", "")},
            source_url=item["url"] if _public_https(item["url"]) else NASDAQ_HALTS_FEED,
            source_type="nasdaq_halts", severity="critical",
            verification_state="investigation_only",
            identity=["nasdaq_halt", ticker or "UNK", item["url"] or item["title"]],
            metadata={"source_published_at": item["published_at"]},
        )
        seen += conn.total_changes > before
    return {"signals_seen": seen, "errors": 0}
