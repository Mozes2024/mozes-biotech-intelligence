"""Primary / near-primary hot feeds: FDA RSS, wire services, Nasdaq trade halts.

Secondary aggregators remain discovery backups; these feeds feed Stage-1 alerts.
"""
from __future__ import annotations

import http.client
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .ir_registry import resolve_alias
from .live_monitor import observe, record_change
from .materiality import classify_outcome

FDA_FEEDS = (
    # fda.gov answers 401 to some networks; verify with the feed-probe workflow, not locally.
    ("fda_press_releases", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"),
    ("fda_drugs", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml"),
    ("fda_biologics", "https://www.fda.gov/AboutFDA/ContactFDA/StayInformed/RSSFeeds/Biologics/rss.xml"),
)

WIRE_FEEDS = (
    ("businesswire_health", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEVlZWA=="),
    ("businesswire_clinical_trials", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeGFNXXw=="),
    ("globenewswire_biotech", "https://rss.globenewswire.com/RssFeed/industry/4573-Biotechnology/feedTitle/GlobeNewswire%20-%20Industry%20News%20on%20Biotechnology"),
    ("globenewswire_clinical_study", "https://rss.globenewswire.com/RssFeed/subjectcode/27-Clinical%20Study/feedTitle/GlobeNewswire%20-%20Clinical%20Study"),
    ("prnewswire_biotech", "https://www.prnewswire.com/rss/health-latest-news/biotechnology-list.rss"),
    ("prnewswire_health", "https://www.prnewswire.com/rss/health-latest-news/health-latest-news-list.rss"),
)

NASDAQ_HALTS_FEED = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
USER_AGENT = "MozesBiotechMonitor/1.1 (+https://github.com/Mozes2024/mozes-biotech-intelligence; Mozes2024@users.noreply.github.com)"

BIOTECHISH = re.compile(
    r"\b(biotech|therapeutics?|pharma(?:ceutical)?s?|oncology|clinical\s+trial|"
    r"phase\s*[123]|fda|pdufa|topline|endpoint|antibody|gene\s+therapy|"
    r"health\s+canada|ema|mhra|pmda|nda|bla|maa|crl|"
    r"approv(?:ed|al|es|ing)|clinical\s+hold|pdufa|"
    r"nasdaq|nyse|ticker)\b",
    re.I,
)
NDAQ = "{http://www.nasdaqtrader.com/}"
NASDAQ_HALTS_PAGE = "https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts"
HALT_CODE_LABELS = {
    "T1": "news pending", "T2": "news released", "T3": "news and resumption times",
    "T12": "additional information requested", "H10": "SEC trading suspension",
    "H11": "regulatory concern", "LUDP": "volatility pause", "LUDS": "volatility pause straddle",
    "M": "volatility pause",
}
NEWS_HALT_CODES = frozenset({"T1", "T2", "T3", "T12", "H10", "H11"})
VOLATILITY_PAUSE_CODES = frozenset({"LUDP", "LUDS", "M"})
STILL_HALTED_HOURS = 72


def _eastern():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001 - tzdata missing on some Windows installs
        return timezone(timedelta(hours=-4))


def fetch_feed(url, *, timeout=12):
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.5"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(4_000_000)


_TRACKING_PARAM = re.compile(r"^(?:utm_\w+|feedref|ref|src|source|cmpid|mc_\w+)$", re.I)


def canonical_url(url):
    """Same release syndicated through several feeds carries per-feed tracking params."""
    parts = urlsplit((url or "").strip())
    if not parts.hostname:
        return url
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not _TRACKING_PARAM.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", parts.netloc.lower(), path, query, ""))


def recorded_release_urls(conn, *, change_type=None, days=10):
    """Canonical URLs of wire releases already recorded recently (cross-lane de-duplication)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = "SELECT source_url FROM change_events WHERE source_type='wire' AND source_url IS NOT NULL AND detected_at>=?"
    args = [cutoff]
    if change_type:
        sql += " AND change_type=?"
        args.append(change_type)
    return {canonical_url(row[0]) for row in conn.execute(sql, args)}


_EXCHANGE_SYMBOL = re.compile(r"\((?:NASDAQ|Nasdaq|NYSE)(?:\s+\w+)?\s*[:：]\s*([A-Z][A-Z0-9.]{0,9})\)")


def _exchange_symbol(text):
    """Wire dateline '(Nasdaq: XXXX)'; more than one distinct symbol is ambiguous."""
    symbols = set(_EXCHANGE_SYMBOL.findall(text or ""))
    if len(symbols) != 1:
        return None
    return {"ticker": symbols.pop(), "source": "wire_dateline_symbol"}


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


def poll_fda_feeds(conn, *, fetch, now=None, max_items=20, resolve_headline=None):
    now = now or datetime.now(timezone.utc)
    from .news_signals import resolve_headline as default_resolve
    resolve_headline = resolve_headline or default_resolve
    seen = errors = 0
    for name, url in FDA_FEEDS:
        try:
            items = _parse_rss_items(fetch(url), now=now, max_age_hours=36)[:max_items]
            observe(conn, f"fda_feed:{name}", {"status": "active", "checked_at": now.isoformat(), "items": len(items)},
                    source_url=url, source_type="fda")
            for item in items:
                text = item["title"] + " " + item.get("summary", "")
                from .clinical_events import ingest_clinical
                if not BIOTECHISH.search(text):
                    ingest_clinical(conn,headline=item['title'],summary=item.get('summary',''),source_url=item['url'],
                                    published_at=item['published_at'],source_type='fda',publish=False)
                    continue
                outcome = classify_outcome(text)
                issuer = resolve_headline(conn, item["title"]) or resolve_alias(text)
                ticker = issuer["ticker"] if issuer else None
                before = conn.total_changes
                record_change(
                    conn, ticker=ticker, change_type="fda_release_signal", previous_value=None,
                    new_value={"headline": item["title"], "published_at": item["published_at"],
                               "summary": item.get("summary", ""), "outcome": outcome},
                    source_url=item["url"], source_type="fda",
                    severity="high" if ticker and outcome.get("material") else "medium",
                    verification_state="investigation_only",
                    identity=["fda", name, item["url"]],
                    metadata={"feed": name, "identity_source": (issuer or {}).get("source"),
                              "source_published_at": item["published_at"]},
                )
                seen += conn.total_changes > before
        except (OSError, ValueError, ET.ParseError, TypeError, http.client.HTTPException) as exc:
            errors += 1
            observe(conn, f"fda_feed:{name}", {"status": "fetch_error", "error": str(exc)[:160],
                                               "checked_at": now.isoformat()},
                    source_url=url, source_type="fda")
    return {"signals_seen": seen, "errors": errors}


def _globe_forward_only_from(conn, name, url, payload, items, now):
    """The reviewed transport transition cannot replay a week of missed releases."""
    if name not in {'globenewswire_biotech', 'globenewswire_clinical_study'}:
        return None
    if urlsplit(url).hostname != 'rss.globenewswire.com':
        raise ValueError('unreviewed GlobeNewswire transport')
    root = ET.fromstring(payload)
    if root.tag != 'rss' or root.find('channel') is None:
        raise ValueError('official GlobeNewswire RSS required')
    for item in items:
        parts = urlsplit(item['url'])
        if (parts.scheme not in {'http','https'} or parts.username or parts.password or parts.port is not None
                or parts.hostname not in {'globenewswire.com','www.globenewswire.com','rss.globenewswire.com'}):
            raise ValueError('nonofficial GlobeNewswire article URL')
    key = 'wire_transport_baseline:globe_rss_v1:' + name
    prior = conn.execute('SELECT value_json FROM monitor_observations WHERE observation_key=?', (key,)).fetchone()
    if prior:
        value = json.loads(prior[0])
        if not isinstance(value, dict):
            raise ValueError('invalid GlobeNewswire activation evidence')
        at = datetime.fromisoformat(value.get('activated_at'))
        if value.get('transport_url') != url or at.tzinfo is None or at > now:
            raise ValueError('invalid GlobeNewswire activation evidence')
        return at
    observe(conn, key, {'transport_url': url, 'activated_at': now.isoformat(), 'policy': 'forward_only_v1'},
            source_url=url, source_type='wire')
    return now


def poll_wire_feeds(conn, *, fetch, now=None, resolve_headline=None, max_items=30):
    """Wire headlines are investigation signals; SEC/FDA remain verification."""
    now = now or datetime.now(timezone.utc)
    from .news_signals import resolve_headline as default_resolve
    supplied_resolver = resolve_headline is not None
    resolve_headline = resolve_headline or default_resolve
    seen = errors = 0
    for name, url in WIRE_FEEDS:
        try:
            payload = fetch(url)
            items = _parse_rss_items(payload, now=now, max_age_hours=168)[:max_items]
            after = _globe_forward_only_from(conn, name, url, payload, items, now)
            before = len(items)
            if after is not None:
                items = [item for item in items if datetime.fromisoformat(item['published_at']) >= after]
            observe(conn, f"wire_feed:{name}", {"status": "active", "checked_at": now.isoformat(), "items": len(items),
                                               "forward_only_from": after.isoformat() if after else None,
                                               "baseline_suppressed": before - len(items)},
                    source_url=url, source_type="wire")
            issuer_lane_urls = recorded_release_urls(conn, change_type="company_release_signal")
            for item in items:
                if canonical_url(item["url"]) in issuer_lane_urls:
                    continue  # already alerted via the per-issuer fallback lane
                text = item["title"] + " " + item.get("summary", "")
                if not BIOTECHISH.search(text):
                    from .clinical_events import ingest_clinical
                    ingest_clinical(conn,headline=item['title'],summary=item.get('summary',''),source_url=item['url'],
                                    published_at=item['published_at'],source_type='wire',publish=False)
                    continue
                issuer = (resolve_headline(conn, item["title"]) or resolve_alias(item["title"])
                          or _exchange_symbol(item.get("summary", "")[:400]))
                ticker = issuer["ticker"] if issuer else None
                from .clinical_events import classify_clinical, resolve_issuer
                verified = resolve_issuer(conn,item['title'],item.get('summary',''))
                if verified:
                    issuer = verified
                    ticker = verified['ticker']
                elif not supplied_resolver:
                    issuer = None
                    ticker = None
                attributions = [(issuer, ticker)]
                if ticker is None and not supplied_resolver:
                    # Joint multi-issuer releases ("Merck and Moderna ...") resolve to
                    # nothing single-issuer, but each named watched issuer is material.
                    from .clinical_events import resolve_named_issuers
                    from .priority import priority_tickers
                    watched = set(priority_tickers())
                    attributions = [
                        ({"ticker": row["ticker"], "cik": row.get("cik"), "source": "joint_release"},
                         row["ticker"])
                        for row in resolve_named_issuers(conn, item["title"], item.get("summary", ""))
                        if row["ticker"] in watched
                    ] or [(None, None)]
                outcome = classify_clinical(text)
                for issuer, ticker in attributions:
                    identity = ["wire", canonical_url(item["url"])]
                    if len(attributions) > 1:
                        identity.append(ticker)
                    before = conn.total_changes
                    record_change(
                        conn, ticker=ticker, change_type="wire_release_signal", previous_value=None,
                        new_value={"headline": item["title"], "published_at": item["published_at"],
                                   "summary": item.get("summary", ""), "outcome": outcome},
                        source_url=item["url"], source_type="wire",
                        severity="high" if ticker and outcome.get("material") else "medium",
                        verification_state="investigation_only",
                        identity=identity,
                        metadata={"feed": name, "identity_source": (issuer or {}).get("source"),
                                  "source_published_at": item["published_at"]},
                    )
                    seen += conn.total_changes > before
        except (OSError, ValueError, ET.ParseError, TypeError, http.client.HTTPException) as exc:
            errors += 1
            observe(conn, f"wire_feed:{name}", {"status": "fetch_error", "error": str(exc)[:160],
                                                "checked_at": now.isoformat()},
                    source_url=url, source_type="wire")
    return {"signals_seen": seen, "errors": errors}


def parse_nasdaq_halts(payload, *, now=None, max_age_hours=8):
    """Parse the ndaq: namespaced halt feed; items carry no <link> and pubDate is the ET date only."""
    now = now or datetime.now(timezone.utc)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    root = ET.fromstring(payload)
    halts = []
    for item in root.findall("./channel/item"):
        symbol = (item.findtext(NDAQ + "IssueSymbol") or item.findtext("title") or "").strip().upper()
        code = (item.findtext(NDAQ + "ReasonCode") or "").strip().upper()
        halt_date = (item.findtext(NDAQ + "HaltDate") or "").strip()
        halt_time = (item.findtext(NDAQ + "HaltTime") or "").strip()
        if not symbol or not code or not halt_date or not halt_time:
            continue
        try:
            local = datetime.strptime(f"{halt_date} {halt_time.split('.')[0]}", "%m/%d/%Y %H:%M:%S")
        except ValueError:
            continue
        halted_at = local.replace(tzinfo=_eastern()).astimezone(timezone.utc)
        resumed = bool((item.findtext(NDAQ + "ResumptionTradeTime") or "").strip())
        horizon = timedelta(hours=max_age_hours if resumed else max(max_age_hours, STILL_HALTED_HOURS))
        if not now - horizon <= halted_at <= now + timedelta(minutes=15):
            continue
        halts.append({
            "ticker": symbol,
            "reason_code": code,
            "issue_name": (item.findtext(NDAQ + "IssueName") or "").strip(),
            "market": (item.findtext(NDAQ + "Market") or "").strip(),
            "halted_at": halted_at.isoformat(),
            "halt_date": halt_date,
            "halt_time": halt_time,
            "resumption_trade_time": (item.findtext(NDAQ + "ResumptionTradeTime") or "").strip() or None,
        })
    return halts


def poll_nasdaq_halts(conn, *, fetch, now=None, watch_tickers=None):
    """News/regulatory halts on watched issuers are among the earliest public catalyst signals."""
    now = now or datetime.now(timezone.utc)
    watch = {t.upper() for t in (watch_tickers or [])}
    try:
        halts = parse_nasdaq_halts(fetch(NASDAQ_HALTS_FEED), now=now)
    except (OSError, ValueError, ET.ParseError, UnicodeError, http.client.HTTPException) as exc:
        observe(conn, "nasdaq_halts", {"status": "fetch_error", "error": str(exc)[:160],
                                       "checked_at": now.isoformat()},
                source_url=NASDAQ_HALTS_FEED, source_type="nasdaq_halts")
        return {"signals_seen": 0, "errors": 1}
    observe(conn, "nasdaq_halts", {"status": "active", "checked_at": now.isoformat(), "items": len(halts)},
            source_url=NASDAQ_HALTS_FEED, source_type="nasdaq_halts")
    seen = 0
    for halt in halts:
        code = halt["reason_code"]
        if halt["ticker"] not in watch:
            continue
        if code in NEWS_HALT_CODES:
            change_type, severity = "nasdaq_halt_signal", "critical"
        elif code in VOLATILITY_PAUSE_CODES:
            change_type, severity = "nasdaq_volatility_pause", "high"
        else:
            continue
        before = conn.total_changes
        record_change(
            conn, ticker=halt["ticker"], change_type=change_type, previous_value=None,
            new_value={"headline": f"{halt['ticker']} trading halt {code} "
                                   f"({HALT_CODE_LABELS.get(code, 'halt')}) at {halt['halt_time']} ET",
                       "published_at": halt["halted_at"], **halt},
            source_url=NASDAQ_HALTS_PAGE, source_type="nasdaq_halts", severity=severity,
            verification_state="investigation_only",
            identity=["nasdaq_halt", halt["ticker"], code, halt["halt_date"], halt["halt_time"]],
            metadata={"source_published_at": halt["halted_at"], "reason_code": code},
        )
        seen += conn.total_changes > before
    return {"signals_seen": seen, "errors": 0}
