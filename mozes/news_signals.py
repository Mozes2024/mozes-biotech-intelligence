"""Bounded secondary-news discovery. Headlines never verify clinical catalysts."""
from __future__ import annotations

import re
import os
import time
import ipaddress
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlsplit

from .live_monitor import observe, record_change
from .priority import priority_tickers

PUBLISHERS = ("reuters.com", "apnews.com", "statnews.com", "fiercebiotech.com",
              "endpts.com", "biopharmadive.com", "businesswire.com", "globenewswire.com")
TOPICS = re.compile(r"\b(trial|clinical|phase\s*[123]|topline|readout|results|endpoint|"
                    r"fda|approval|milestone|offering|financing|acquisition|merger)\b", re.I)
MATERIAL = re.compile(r"\b(phase\s*[23](?:\s*/\s*3)?|top[ -]?line(?: data)?|read[ -]?out|"
                      r"PDUFA|FDA (?:decision|approv\w*)|interim analysis|data expected)\b", re.I)
DIRECT_FEEDS = ('https://www.fiercebiotech.com/rss/xml',
                'https://www.biopharmadive.com/feeds/news/')
GLOBAL_QUERIES = (
    '(biotech OR therapeutics OR pharmaceutical) ("phase 2" OR "phase 3" OR "topline" OR "readout") when:7d',
    '(biotech OR therapeutics OR pharmaceutical) (PDUFA OR "FDA decision" OR "interim analysis" OR "data expected") when:7d',
)


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


def parse_feed(payload, *, now=None, max_age_hours=48, publisher_url=None):
    """Use the publisher supplied by the feed; missing dates or sources are excluded."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for item in ET.fromstring(payload).findall("./channel/item"):
        title_node = item.find("title")
        title = "".join(title_node.itertext()).strip() if title_node is not None else ""
        link = (item.findtext("link") or "").strip()
        source = item.find("source")
        origin = publisher_url or (source.get("url") if source is not None else "") or ""
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
        except (TypeError, ValueError, IndexError):
            if publisher_url == 'https://www.fiercebiotech.com/rss/xml':
                try:
                    published = datetime.strptime(item.findtext('pubDate') or '', '%b %d, %Y %I:%M%p').replace(
                        tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc)
                except ValueError:
                    continue
            else:
                continue
        if publisher_url and not _same_official_host(link, publisher_url):
            continue
        if not title or not _public_https(link) or not _trusted_host(origin):
            continue
        if not now - timedelta(hours=max_age_hours) <= published <= now + timedelta(minutes=10):
            continue
        rows.append({"title": title, "url": link, "publisher_url": origin,
                     "summary": "".join(item.find("description").itertext()) if item.find("description") is not None else "",
                     "published_at": published.isoformat()})
    return rows


def resolve_headline(conn, headline, mappings=None):
    """Use the conservative SEC equity projection, never infer a ticker from prose."""
    from .universe import normalize_org
    text = " " + normalize_org(headline) + " "
    matches = {}
    symbols = {symbol.upper() for symbol in re.findall(r'(?:NASDAQ|NYSE)\s*[:：]\s*([A-Z][A-Z0-9.]{0,9})\b', headline, re.I)}
    if mappings is None:
        mappings = conn.execute("SELECT * FROM sponsor_ticker_map WHERE source='SEC-v2C-equity' AND confidence>=0.85 AND cik IS NOT NULL")
    for raw in mappings:
        row = raw if isinstance(raw, dict) else dict(raw)
        name = row.get('_headline_name') or normalize_org(row['sponsor'])
        if row['ticker'] in symbols or (len(name) >= 6 and " " + name + " " in text):
            matches[(row['ticker'], row['cik'])] = dict(row)
    if len(matches) != 1:
        return None
    result = dict(next(iter(matches.values())))
    result.pop('_headline_name', None)
    return result


def verify_discovered(conn, issuers, *, fetch, now, deadline):
    """Registry estimates remain discovery; promotion uses the existing SEC matcher."""
    from .discovery import study_to_candidate, store_candidates
    errors, checked = [], []
    for issuer in issuers:
        if time.monotonic() >= deadline:
            break
        ticker = issuer['ticker']
        key = 'news_verification:' + ticker
        prior = conn.execute('SELECT value_json FROM monitor_observations WHERE observation_key=?', (key,)).fetchone()
        if prior:
            stamp = json.loads(prior[0]).get('checked_at')
            retry_hours = 1 if json.loads(prior[0]).get('status') == 'fetch_error' else 24
            if stamp and now - datetime.fromisoformat(stamp) < timedelta(hours=retry_hours):
                continue
        status = 'attempted'
        try:
            url = 'https://clinicaltrials.gov/api/v2/studies?' + urllib.parse.urlencode({
                'query.spons': issuer['sponsor'], 'pageSize': 20, 'format': 'json',
                'sort': 'LastUpdatePostDate:desc'})
            data = json.loads(fetch(url))
            candidates = [study_to_candidate(study, [issuer]) for study in data.get('studies', [])[:20]]
            # Collaborator-only hits must not inherit the discovered issuer identity.
            candidates = [c for c in candidates if c.get('ticker') == ticker and c.get('nct_id')]
            store_candidates(conn, candidates)
            checked.append(ticker)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            status = 'fetch_error'
            errors.append({'ticker': ticker, 'source': 'clinicaltrials', 'error': str(exc)[:160]})
        # A registry outage must not suppress independent SEC verification.
        if os.environ.get('SEC_USER_AGENT') and time.monotonic() < deadline:
            try:
                from .refresh import verify_candidates_from_sec, scan_watch_universe_regulatory
                clinical = verify_candidates_from_sec(conn, filings_per_company=3, deadline=deadline,
                                                      tickers={ticker})
                if time.monotonic() < deadline:
                    regulatory = scan_watch_universe_regulatory(conn, filings_per_company=2, deadline=deadline,
                                                               tickers={ticker})
                else:
                    regulatory = {'budget_exhausted': True}
                for result in (clinical, regulatory):
                    if result and (result.get('errors') or result.get('budget_exhausted')):
                        status = 'fetch_error'
                        errors.extend(result.get('errors') or [{'ticker': ticker, 'source': 'sec', 'error': 'verification_budget_exhausted'}])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                status = 'fetch_error'
                errors.append({'ticker': ticker, 'source': 'sec', 'error': str(exc)[:160]})
        observe(conn, key, {'status': status, 'checked_at': now.isoformat()}, source_type='clinicaltrials')
    return {'checked': checked, 'errors': errors, 'budget_exhausted': time.monotonic() >= deadline}


def discover_news_issuers(conn, *, fetch, now, max_issuers=4):
    """Two publisher feeds and two queries, 40 headlines each, four identities per pass."""
    from . import db
    enrolled, signals, errors, candidates = {}, 0, [], {}
    from .universe import normalize_org
    mappings = [{**dict(row), '_headline_name': normalize_org(row['sponsor'])} for row in conn.execute(
        "SELECT * FROM sponsor_ticker_map WHERE source='SEC-v2C-equity' AND confidence>=0.85 AND cik IS NOT NULL")]
    feeds = [(url, url) for url in DIRECT_FEEDS]
    feeds += [('https://news.google.com/rss/search?q=' + urllib.parse.quote(query) + '&hl=en-US&gl=US&ceid=US:en', None)
              for query in GLOBAL_QUERIES]
    for url, origin in feeds:
        try:
            for item in parse_feed(fetch(url), now=now, max_age_hours=168, publisher_url=origin)[:40]:
                if not MATERIAL.search(item['title'] + ' ' + item.get('summary', '')):
                    continue
                issuer = resolve_headline(conn, item['title'], mappings)
                if issuer:
                    candidates.setdefault(issuer['ticker'], {'issuer': issuer, 'items': []})['items'].append(item)
        except (OSError, ValueError, ET.ParseError) as exc:
            errors.append({'url': url, 'error': str(exc)[:160]})
    # Oldest discovery scan first: repeated headlines cannot starve new identities.
    scanned_at = {row['observation_key'].split(':', 1)[1]: row['observed_at'] for row in conn.execute(
        "SELECT observation_key,observed_at FROM monitor_observations WHERE observation_key LIKE 'news_discovery:%'")}
    for ticker in sorted(candidates, key=lambda t: (scanned_at.get(t, ''), t))[:max_issuers]:
        issuer = candidates[ticker]['issuer']
        enrolled[ticker] = issuer
        watch = conn.execute('SELECT 1 FROM watch_universe WHERE ticker=?', (ticker,)).fetchone()
        if not watch:
            db.upsert_watch(conn, ticker, company=issuer['sponsor'], cik=issuer['cik'], source='dynamic_news_discovery')
        for item in candidates[ticker]['items'][:6]:
            before = conn.total_changes
            record_change(conn, ticker=ticker, change_type='news_signal', previous_value=None,
                          new_value={'headline': item['title'], 'publisher': urlsplit(item['publisher_url']).hostname,
                                     'published_at': item['published_at']},
                          source_url=item['url'], source_type='secondary_news', severity='low',
                          verification_state='investigation_only',
                          identity=['news', ticker, re.sub(r'\W+', ' ', item['title'].lower()).strip()],
                          metadata={'publisher_url': item['publisher_url'], 'not_clinical_verification': True,
                                    'discovery_origin': 'sector_headline', 'identity_source': issuer['source']})
            signals += conn.total_changes > before
        observe(conn, 'news_discovery:' + ticker, now.isoformat(), source_type='secondary_news')
    verification = verify_discovered(conn, list(enrolled.values()), fetch=fetch, now=now,
                                    deadline=time.monotonic() + 30)
    return {'issuers': list(enrolled), 'signals_seen': signals, 'errors': errors,
            'verification': verification}, list(enrolled.values())


def poll_news(conn, *, limit=8, fetch=None, now=None):
    """Store relevant reporting as investigation signals, never as event evidence."""
    now = now or datetime.now(timezone.utc)
    def default_fetch(url):
        with urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "MozesBiotechMonitor/1.0"}), timeout=5) as response:
            return response.read(4_000_000)
    fetch = fetch or default_fetch
    dynamic, discovered = discover_news_issuers(conn, fetch=fetch, now=now)
    issuers = conn.execute(
        "SELECT d.ticker, MIN(COALESCE(m.sponsor,d.sponsor)) company "
        "FROM discovery_candidates d LEFT JOIN sponsor_ticker_map m "
        "ON m.ticker=d.ticker AND m.source='SEC-v2C-equity' "
        "WHERE d.ticker IS NOT NULL AND (UPPER(d.phase) LIKE '%PHASE3%' OR UPPER(d.phase) LIKE '%PHASE2%') "
        "GROUP BY d.ticker ORDER BY COALESCE((SELECT observed_at FROM monitor_observations "
        "WHERE observation_key='news_poll:'||d.ticker),'') ASC,d.ticker").fetchall()
    issuers = [dict(row) for row in issuers]
    present = {row['ticker'] for row in issuers}
    issuers += [{'ticker': row['ticker'], 'company': row['company']} for row in conn.execute(
        "SELECT ticker,company FROM watch_universe WHERE active=1 AND source='dynamic_news_discovery' ORDER BY updated_at") if row['ticker'] not in present]
    polled = {row['observation_key'].split(':', 1)[1]: row['observed_at'] for row in conn.execute(
        "SELECT observation_key,observed_at FROM monitor_observations WHERE observation_key LIKE 'news_poll:%'")}
    issuers.sort(key=lambda row: (polled.get(row['ticker'], ''), row['ticker']))
    preferred = set(priority_tickers())
    priority = [row for row in issuers if row['ticker'] in preferred][:min(limit, 2)]
    issuers = priority + [row for row in issuers if row['ticker'] not in preferred][:max(0, limit - len(priority))]
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
            observe(conn, 'news_poll:' + ticker, {'checked_at': now.isoformat(), 'status': 'fetch_error'}, source_type='secondary_news')
    official_issuers = {row['ticker']: row for row in issuers}
    official_issuers.update({row['ticker']: row for row in discovered})
    official = poll_official_feeds(conn, list(official_issuers.values()), fetch=fetch, now=now)
    return {"checked": checked, "signals_seen": added + dynamic["signals_seen"], "errors": errors, "official_feeds": official, "discovery": dynamic}
