"""Bounded secondary-news discovery. Headlines never verify clinical catalysts."""
from __future__ import annotations

import re
import ipaddress
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from .live_monitor import observe, record_change
from .priority import priority_tickers

PUBLISHERS = ("reuters.com", "apnews.com", "statnews.com", "fiercebiotech.com",
              "endpts.com", "biopharmadive.com")
TOPICS = re.compile(r"\b(trial|clinical|phase\s*[123]|topline|readout|results|endpoint|"
                    r"fda|approval|milestone|offering|financing|acquisition|merger)\b", re.I)
OFFICIAL_IR_SITES = json.loads((Path(__file__).parent / "data" / "official_ir_sites.json").read_text(encoding="utf-8"))


def _public_https(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        valid_authority = not parts.username and not parts.password and parts.port in (None, 443)
    except (TypeError, ValueError):
        return False
    if parts.scheme != "https" or not host or not valid_authority:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return "." in host and not host.endswith((".local", ".internal"))


def _same_official_host(url, site):
    host = (urlsplit(url).hostname or "").lower()
    site_host = (urlsplit(site).hostname or "").lower()
    return _public_https(url) and (host == site_host or host.endswith("." + site_host))


class _FeedLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        if "alternate" in a.get("rel", "").lower().split() and a.get("type", "").lower() in {
                "application/rss+xml", "application/atom+xml"} and a.get("href"):
            self.links.append(a["href"])


def discover_official_feed(site, fetch):
    """Accept only a feed advertised by an authoritative HTTPS IR page."""
    if not _public_https(site):
        return None
    parser = _FeedLinks()
    parser.feed(fetch(site).decode("utf-8", errors="replace"))
    for href in parser.links:
        feed_url = urllib.parse.urljoin(site, href)
        if _same_official_host(feed_url, site):
            return feed_url
    return None


def parse_official_feed(payload, *, site, now=None, max_age_hours=48):
    now = now or datetime.now(timezone.utc)
    root = ET.fromstring(payload)
    if root.tag == "rss":
        entries = [(item.findtext("title"), item.findtext("link"), item.findtext("pubDate"))
                   for item in root.findall("./channel/item")]
    elif root.tag == "{http://www.w3.org/2005/Atom}feed":
        ns = "{http://www.w3.org/2005/Atom}"
        entries = [(item.findtext(ns + "title"), next((link.get("href") for link in item.findall(ns + "link")
                     if link.get("rel", "alternate") == "alternate"), None),
                     item.findtext(ns + "published") or item.findtext(ns + "updated"))
                   for item in root.findall(ns + "entry")]
    else:
        return []
    rows = []
    for title, link, stamp in entries:
        try:
            published = parsedate_to_datetime(stamp).astimezone(timezone.utc) if root.tag == "rss" else datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (AttributeError, TypeError, ValueError, IndexError):
            continue
        if title and link and _same_official_host(link, site) and now - timedelta(hours=max_age_hours) <= published <= now + timedelta(minutes=10):
            rows.append({"title": title.strip(), "url": link, "published_at": published.isoformat()})
    return rows


def poll_official_feeds(conn, issuers, *, fetch, now):
    """Follow official company releases for the bounded late-stage news cohort."""
    checked = added = 0
    errors = []
    for issuer in issuers:
        ticker = issuer["ticker"]
        checked_at = now.isoformat()
        sites = [OFFICIAL_IR_SITES[ticker]] if ticker in OFFICIAL_IR_SITES else []
        sites += [row[0] for row in conn.execute(
            "SELECT DISTINCT es.url FROM event_sources es JOIN events e ON e.id=es.event_id "
            "WHERE json_extract(e.payload,'$.ticker')=? AND es.source_type='company_ir' "
            "AND es.url IS NOT NULL", (ticker,))]
        site = next((url for url in sites if _public_https(url)), None)
        if not site:
            observe(conn, "official_feed:" + ticker, {"status": "no_official_site", "checked_at": checked_at}, source_type="company_ir")
            continue
        try:
            feed = discover_official_feed(site, fetch)
            if not feed:
                observe(conn, "official_feed:" + ticker, {"status": "no_rss_found", "site": site, "checked_at": checked_at},
                        source_url=site, source_type="company_ir")
                continue
            items = parse_official_feed(fetch(feed), site=site, now=now)
            checked += 1
            observe(conn, "official_feed:" + ticker, {"status": "active", "site": site, "feed": feed, "checked_at": checked_at},
                    source_url=feed, source_type="company_ir")
            for item in items[:10]:
                before = conn.total_changes
                record_change(conn, ticker=ticker, change_type="company_release_signal", previous_value=None,
                              new_value={"headline": item["title"], "published_at": item["published_at"]},
                              source_url=item["url"], source_type="company_ir", severity="medium",
                              verification_state="investigation_only", identity=["company_release", ticker, item["url"]],
                              metadata={"feed_url": feed, "headline_only": True})
                added += conn.total_changes > before
        except (OSError, ValueError, ET.ParseError, UnicodeError) as exc:
            errors.append({"ticker": ticker, "error": str(exc)[:160]})
            observe(conn, "official_feed:" + ticker, {"status": "fetch_error", "site": site, "checked_at": checked_at},
                    source_url=site, source_type="company_ir")
    return {"checked": checked, "signals_seen": added, "errors": errors}


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
            return response.read(4_000_000)
    fetch = fetch or default_fetch
    issuers = conn.execute(
        "SELECT d.ticker, MIN(COALESCE(m.sponsor,d.sponsor)) company "
        "FROM discovery_candidates d LEFT JOIN sponsor_ticker_map m "
        "ON m.ticker=d.ticker AND m.source='SEC-v2C-equity' "
        "WHERE d.ticker IS NOT NULL AND UPPER(d.phase) LIKE '%PHASE3%' "
        "GROUP BY d.ticker ORDER BY COALESCE((SELECT observed_at FROM monitor_observations "
        "WHERE observation_key='news_poll:'||d.ticker),'') ASC,d.ticker").fetchall()
    preferred = set(priority_tickers())
    issuers = [row for row in issuers if row['ticker'] in preferred][:min(limit, 2)] + [
        row for row in issuers if row['ticker'] not in preferred][:max(0, limit - min(limit, 2))]
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
    official = poll_official_feeds(conn, issuers, fetch=fetch, now=now)
    return {"checked": checked, "signals_seen": added, "errors": errors, "official_feeds": official}
