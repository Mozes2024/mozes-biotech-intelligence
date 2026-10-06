#!/usr/bin/env python3
"""Fetch each hot-lane feed once, report status/format, and save a real sample fixture.

Usage: python scripts/capture_feed_fixtures.py [--save]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mozes.primary_feeds import FDA_FEEDS, NASDAQ_HALTS_FEED, USER_AGENT, WIRE_FEEDS  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "feeds"


def probe(name, url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(4_000_000)
            status = response.status
            ctype = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return {"name": name, "url": url, "status": exc.code, "ok": False}, None
    except (OSError, urllib.error.URLError) as exc:
        return {"name": name, "url": url, "status": "ERR", "error": str(exc)[:160], "ok": False}, None
    info = {"name": name, "url": url, "status": status, "content_type": ctype, "bytes": len(body)}
    try:
        root = ET.fromstring(body)
        items = root.findall("./channel/item") or root.findall("{http://www.w3.org/2005/Atom}entry")
        info.update(xml=True, root=root.tag, items=len(items),
                    channel_title=(root.findtext("./channel/title") or "").strip(),
                    namespaces=sorted({el.tag.split('}')[0][1:] for el in root.iter() if el.tag.startswith('{')}))
        if items:
            first = items[0]
            info["first_item_children"] = [child.tag for child in first]
    except ET.ParseError as exc:
        info.update(xml=False, parse_error=str(exc)[:120], head=body[:200].decode("utf-8", "replace"))
    info["ok"] = status == 200 and info.get("xml") is True
    return info, body


FDA_RSS_INDEX = "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds"


def list_fda_rss():
    request = urllib.request.Request(FDA_RSS_INDEX, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        html = response.read(2_000_000).decode("utf-8", "replace")
    import re
    return sorted(set(re.findall(r'href="([^"]*rss[^"]*\.xml)"', html)))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--list-fda", action="store_true", help="print RSS links advertised by fda.gov")
    parser.add_argument("--extra", default="", help="space-separated candidate URLs to probe (not saved)")
    args = parser.parse_args(argv)
    for index, url in enumerate(args.extra.split()):
        print(json.dumps(probe(f"extra_{index}", url)[0], indent=2))
    if args.list_fda:
        try:
            print(json.dumps({"fda_rss_links": list_fda_rss()}, indent=2))
        except (OSError, urllib.error.URLError) as exc:
            print(json.dumps({"fda_rss_links_error": str(exc)[:160]}))
    feeds = list(FDA_FEEDS) + list(WIRE_FEEDS) + [("nasdaq_halts", NASDAQ_HALTS_FEED)]
    report = []
    for name, url in feeds:
        info, body = probe(name, url)
        report.append(info)
        if args.save and info.get("ok") and body:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / f"{name}.xml").write_bytes(body)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
