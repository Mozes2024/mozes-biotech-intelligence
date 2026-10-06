"""Bounded secondary-news discovery. Headlines never verify clinical catalysts."""
from __future__ import annotations

import http.client
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
from .ir_registry import load_ir_registry, ir_site

PUBLISHERS = ("reuters.com", "apnews.com", "statnews.com", "fiercebiotech.com",
              "endpts.com", "biopharmadive.com", "businesswire.com", "globenewswire.com",
              "prnewswire.com", "accesswire.com")
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


OFFICIAL_IR_SITES = {ticker: row["site"] for ticker, row in load_ir_registry().items()}


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


class _PressLinks(HTMLParser):
    """Collect same-host HTTPS anchors that look like press-release index entries."""

    def __init__(self, site):
        super().__init__()
        self.site = site
        self.links = []
        self.entries = []
        self._current = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        url = urllib.parse.urljoin(self.site, href)
        if not _same_official_host(url, self.site):
            return
        path = (urlsplit(url).path or "").lower()
        if any(token in path for token in ("press", "news", "release", "media", "announcement")):
            self.links.append(url)
            self._current, self._text = url, []

    def handle_data(self, data):
        if self._current:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current:
            self.entries.append({"url": self._current, "title": " ".join("".join(self._text).split())})
            self._current, self._text = None, []


_ARTICLE_SLUG = re.compile(r"(?:\d{4,}|[a-z0-9]+(?:-[a-z0-9]+){3,})", re.I)


def _looks_like_release(entry):
    """Index/navigation links ('press-releases', 'default.aspx') are not releases."""
    last = (urlsplit(entry["url"]).path or "").rstrip("/").rsplit("/", 1)[-1]
    title = entry.get("title") or ""
    return bool(_ARTICLE_SLUG.search(last)) and len(title) >= 25 and len(title.split()) >= 4


def discover_press_index(site, fetch, *, entries=False):
    """When IR advertises no RSS, fall back to same-host press/news index links."""
    if not _public_https(site):
        return []
    raw = fetch(site)
    html = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    parser = _PressLinks(site)
    parser.feed(html)
    if entries:
        seen, out = set(), []
        for entry in parser.entries:
            if entry["url"] not in seen and _looks_like_release(entry):
                seen.add(entry["url"])
                out.append(entry)
        return out[:12]
    # Deduplicate while preserving order; cap crawl fan-out.
    return list(dict.fromkeys(parser.links))[:12]


def poll_official_feeds(conn, issuers, *, fetch, now):
    """Follow official company releases for the bounded late-stage news cohort."""
    checked = added = 0
    errors = []
    registry = load_ir_registry()
    for issuer in issuers:
        ticker = issuer["ticker"]
        checked_at = now.isoformat()
        entry = registry.get(ticker) or {}
        sites = []
        if entry.get("site"):
            sites.append(entry["site"])
        elif ticker in OFFICIAL_IR_SITES:
            sites.append(OFFICIAL_IR_SITES[ticker])
        sites += [row[0] for row in conn.execute(
            "SELECT DISTINCT es.url FROM event_sources es JOIN events e ON e.id=es.event_id "
            "WHERE json_extract(e.payload,'$.ticker')=? AND es.source_type='company_ir' "
            "AND es.url IS NOT NULL", (ticker,))]
        site = next((url for url in sites if _public_https(url)), None)
        if not site:
            observe(conn, "official_feed:" + ticker, {"status": "no_official_site", "checked_at": checked_at}, source_type="company_ir")
            continue
        try:
            known_feed = entry.get("feed_url")
            feed = known_feed if (known_feed and _same_official_host(known_feed, site)) else discover_official_feed(site, fetch)
            items = []
            feed_url = feed
            if feed:
                items = parse_official_feed(fetch(feed), site=site, now=now)
                checked += 1
                observe(conn, "official_feed:" + ticker, {"status": "active", "site": site, "feed": feed,
                                                          "checked_at": checked_at, "known_feed": bool(known_feed)},
                        source_url=feed, source_type="company_ir")
            else:
                # no_rss_found is no longer a dead end: HTML press index is Stage-1 discovery.
                entries = discover_press_index(site, fetch, entries=True)
                observe(conn, "official_feed:" + ticker,
                        {"status": "html_fallback" if entries else "no_rss_found",
                         "site": site, "index_links": len(entries), "checked_at": checked_at},
                        source_url=site, source_type="company_ir")
                feed_url = site
                # An HTML index has no dates: the first sighting is a baseline, only later additions are news.
                seen_key = "official_html_seen:" + ticker
                prior = conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key=?",
                                     (seen_key,)).fetchone()
                seen_urls = set(json.loads(prior[0]).get("urls", [])) if prior else None
                if entries:
                    merged = list(dict.fromkeys([e["url"] for e in entries] + sorted(seen_urls or [])))[:200]
                    observe(conn, seen_key, {"urls": merged}, source_url=site, source_type="company_ir")
                    checked += 1
                if seen_urls is not None:
                    for entry in entries[:5]:
                        if entry["url"] not in seen_urls:
                            items.append({"title": entry["title"], "url": entry["url"],
                                          "published_at": now.isoformat()})
            for item in items[:10]:
                before = conn.total_changes
                record_change(conn, ticker=ticker, change_type="company_release_signal", previous_value=None,
                              new_value={"headline": item["title"], "published_at": item["published_at"]},
                              source_url=item["url"], source_type="company_ir", severity="medium",
                              verification_state="investigation_only", identity=["company_release", ticker, item["url"]],
                              metadata={"feed_url": feed_url, "headline_only": True,
                                        "html_fallback": not bool(feed)})
                added += conn.total_changes > before
        except (OSError, ValueError, ET.ParseError, UnicodeError, http.client.HTTPException) as exc:
            errors.append({"ticker": ticker, "error": f"{type(exc).__name__}: {exc}"[:160]})
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
    """Registry estimates remain discovery; promotion uses the existing SEC matcher.

    Material headlines escalate retries in minutes (0/1/3/10/30/60), not a flat 24h cooldown.
    """
    from .discovery import study_to_candidate, store_candidates
    from .materiality import classify_outcome, MATERIAL_HEADLINE
    errors, checked = [], []
    material_schedule_hours = (0, 1 / 60, 3 / 60, 10 / 60, 30 / 60, 1.0)
    for issuer in issuers:
        if time.monotonic() >= deadline:
            break
        ticker = issuer['ticker']
        key = 'news_verification:' + ticker
        prior_row = conn.execute('SELECT value_json FROM monitor_observations WHERE observation_key=?', (key,)).fetchone()
        material = bool(issuer.get('material') or MATERIAL_HEADLINE.search(str(issuer.get('headline') or '')))
        if prior_row:
            prior = json.loads(prior_row[0])
            stamp = prior.get('checked_at')
            attempts = int(prior.get('attempts') or 1)
            if prior.get('status') == 'fetch_error':
                retry_hours = 1 / 60
            elif material:
                idx = min(attempts, len(material_schedule_hours) - 1)
                retry_hours = material_schedule_hours[idx]
            else:
                retry_hours = 24
            if stamp and now - datetime.fromisoformat(stamp) < timedelta(hours=retry_hours):
                continue
            next_attempts = attempts + 1
        else:
            next_attempts = 1
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
        observe(conn, key, {'status': status, 'checked_at': now.isoformat(),
                            'attempts': next_attempts, 'material': material,
                            'outcome': classify_outcome(str(issuer.get('headline') or ''))},
                source_type='clinicaltrials')
    return {'checked': checked, 'errors': errors, 'budget_exhausted': time.monotonic() >= deadline}


def discover_news_issuers(conn, *, fetch, now, max_issuers=8):
    """Two publisher feeds and two queries; budget is request-priority, not a hard four-name starve."""
    from . import db
    enrolled, signals, errors, candidates = {}, 0, [], {}
    from .universe import normalize_org
    from .materiality import MATERIAL_HEADLINE
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
                    bucket = candidates.setdefault(issuer['ticker'], {'issuer': issuer, 'items': [], 'material': False})
                    bucket['items'].append(item)
                    if MATERIAL_HEADLINE.search(item['title'] + ' ' + item.get('summary', '')):
                        bucket['material'] = True
        except (OSError, ValueError, ET.ParseError) as exc:
            errors.append({'url': url, 'error': str(exc)[:160]})
    # Material + never-scanned first; aging bonus for oldest unverified.
    scanned_at = {row['observation_key'].split(':', 1)[1]: row['observed_at'] for row in conn.execute(
        "SELECT observation_key,observed_at FROM monitor_observations WHERE observation_key LIKE 'news_discovery:%'")}
    ordered = sorted(
        candidates,
        key=lambda t: (0 if candidates[t]['material'] else 1, scanned_at.get(t, ''), t),
    )[:max_issuers]
    for ticker in ordered:
        issuer = dict(candidates[ticker]['issuer'])
        issuer['material'] = candidates[ticker]['material']
        issuer['headline'] = candidates[ticker]['items'][0]['title']
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
    # Always include the configured hot IR cohort, even before discovery candidates exist.
    for ticker in priority_tickers():
        if ticker in official_issuers:
            continue
        entry = load_ir_registry().get(ticker) or {}
        official_issuers[ticker] = {
            "ticker": ticker,
            "company": entry.get("company") or ticker,
        }
    official = poll_official_feeds(conn, list(official_issuers.values()), fetch=fetch, now=now)
    return {"checked": checked, "signals_seen": added + dynamic["signals_seen"], "errors": errors, "official_feeds": official, "discovery": dynamic}
