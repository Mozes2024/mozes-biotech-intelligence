"""Bounded secondary-news discovery. Headlines never verify clinical catalysts."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .live_monitor import observe, record_change

PUBLISHERS = ("reuters.com", "apnews.com", "statnews.com", "fiercebiotech.com",
              "endpts.com", "biopharmadive.com")
TOPICS = re.compile(r"\b(trial|clinical|phase\s*[123]|topline|readout|results|endpoint|"
                    r"fda|approval|milestone|offering|financing|acquisition|merger)\b", re.I)


def _trusted_host(url):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in PUBLISHERS)


def parse_feed(payload, *, now=None, max_age_hours=48):
    """Use the publisher supplied by the feed; missing dates or sources are excluded."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for item in ET.fromstring(payload).findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source = item.find("source")
        publisher_url = (source.get("url") if source is not None else "") or ""
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError):
            continue
        if not title or not link.startswith("https://") or not _trusted_host(publisher_url):
            continue
        if not now - timedelta(hours=max_age_hours) <= published <= now + timedelta(minutes=10):
            continue
        rows.append({"title": title, "url": link, "publisher_url": publisher_url,
                     "published_at": published.isoformat()})
    return rows


def poll_news(conn, *, limit=8, fetch=None, now=None):
    """Store relevant reporting as investigation signals, never as event evidence."""
    now = now or datetime.now(timezone.utc)
    def default_fetch(url):
        with urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "MozesBiotechMonitor/1.0"}), timeout=5) as response:
            return response.read(512_000)
    fetch = fetch or default_fetch
    issuers = conn.execute(
        "SELECT d.ticker, MIN(COALESCE(m.sponsor,d.sponsor)) company "
        "FROM discovery_candidates d LEFT JOIN sponsor_ticker_map m "
        "ON m.ticker=d.ticker AND m.source='SEC-v2C-equity' "
        "WHERE d.ticker IS NOT NULL AND UPPER(d.phase) LIKE '%PHASE3%' "
        "GROUP BY d.ticker ORDER BY COALESCE((SELECT observed_at FROM monitor_observations "
        "WHERE observation_key='news_poll:'||d.ticker),'') ASC,d.ticker LIMIT ?", (limit,)).fetchall()
    checked = added = 0
    errors = []
    for issuer in issuers:
        ticker, company = issuer['ticker'], issuer['company']
        if not company:
            continue
        query = ('"' + company.replace('"', '') + '" '
                 '(trial OR clinical OR FDA OR results OR offering) when:2d')
        url = 'https://news.google.com/rss/search?q=' + urllib.parse.quote(query) + '&hl=en-US&gl=US&ceid=US:en'
        try:
            items = parse_feed(fetch(url), now=now)
            checked += 1
            for item in items[:6]:
                if not TOPICS.search(item['title']):
                    continue
                identity = ['news', ticker, re.sub(r'\W+', ' ', item['title'].lower()).strip()]
                before = conn.total_changes
                record_change(
                    conn, ticker=ticker, change_type='news_signal', previous_value=None,
                    new_value={"headline": item['title'], "publisher": urlsplit(item['publisher_url']).hostname,
                               "published_at": item['published_at']},
                    source_url=item['url'], source_type='secondary_news', severity='low',
                    verification_state='investigation_only', identity=identity,
                    metadata={"publisher_url": item['publisher_url'],
                              "not_clinical_verification": True})
                added += conn.total_changes > before
            observe(conn, 'news_poll:' + ticker, now.isoformat(), source_type='secondary_news')
        except (OSError, ValueError, ET.ParseError) as exc:
            errors.append({"ticker": ticker, "error": str(exc)[:160]})
    return {"checked": checked, "signals_seen": added, "errors": errors}
