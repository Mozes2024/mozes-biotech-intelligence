"""Read-only official TYRA source diagnostic. Never opens a monitor database."""
import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mozes.ir_registry import load_ir_registry
from mozes.news_signals import _same_official_host, discover_press_index, parse_official_feed
from mozes.primary_feeds import USER_AGENT


def inspect(*, opener=urllib.request.urlopen, now=None):
    now = now or datetime.now(timezone.utc)
    entry = load_ir_registry()['TYRA']
    site = entry['site']
    results = []
    for url in (entry['feed_url'], site, site + 'news-events/news-releases'):
        row = {'url': url, 'checked_at': now.isoformat(), 'operational': False}
        results.append(row)
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
                                             'Accept': 'application/rss+xml, application/xml, text/html'})
            with opener(request, timeout=20) as response:
                final = response.geturl()
                row.update(http_status=response.status, final_url=final,
                           official_host=_same_official_host(final, site))
                if response.status != 200 or not row['official_host']:
                    continue
                body = response.read(2_000_001)
                if len(body) > 2_000_000:
                    raise ValueError('response too large')
            if url == entry['feed_url']:
                root = ET.fromstring(body)
                if root.tag != 'rss':
                    raise ValueError('expected RSS document')
                items = root.findall('./channel/item')
                validated = []
                for item in items:
                    stamp = item.findtext('pubDate') or ''
                    date = parsedate_to_datetime(stamp)
                    if date.tzinfo is None or date > now:
                        raise ValueError('invalid publication date')
                    link = item.findtext('link') or ''
                    if not _same_official_host(link, site):
                        raise ValueError('nonofficial article URL')
                    validated.append({'url': link, 'published_at': date.isoformat()})
                row.update(xml=True, items=len(items), articles=validated,
                           recent_items=len(parse_official_feed(body, site=site, now=now)),
                           operational=bool(items))
            else:
                links = discover_press_index(site, lambda _: body, entries=True)
                row.update(html_articles=len(links), operational=bool(links))
        except urllib.error.HTTPError as exc:
            row.update(http_status=exc.code, error='HTTP ' + str(exc.code))
        except (OSError, ValueError, ET.ParseError) as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'[:160]
    return {'read_only': True, 'company': entry['company'], 'sources': results}


if __name__ == '__main__':
    print(json.dumps(inspect(), indent=2))
