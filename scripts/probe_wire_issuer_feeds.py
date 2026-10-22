#!/usr/bin/env python3
"""Bounded read-only probe of the per-issuer GlobeNewswire fallback feeds.

Runs on a GitHub runner (the network path production uses). It never touches production state:
every database is a throwaway temp file. It checks, for each registry entry with wire_feed_url:
real RSS XML, issuer contributor filtering, forward-only activation, and de-duplication against
the existing categorical wire feeds.
"""
from __future__ import annotations

import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mozes import db  # noqa: E402
from mozes.ir_registry import ir_entries  # noqa: E402
from mozes.live_monitor import observe  # noqa: E402
from mozes.news_signals import _poll_wire_issuer, _wire_issuer_baseline, parse_wire_issuer_feed  # noqa: E402
from mozes.primary_feeds import WIRE_FEEDS, canonical_url, fetch_feed, poll_wire_feeds  # noqa: E402

DC = "{http://dublincore.org/documents/dcmi-namespace/}"


def main():
    now = datetime.now(timezone.utc)
    entries = [e for e in ir_entries() if e.get("wire_feed_url")]
    report = {"generated_at": now.isoformat(), "feed_count": len(entries), "feeds": [], "checks": {}}
    payloads = {}
    for entry in entries:
        row = {"ticker": entry["ticker"], "url": entry["wire_feed_url"], "contributor": entry["wire_contributor"]}
        try:
            body = fetch_feed(entry["wire_feed_url"], timeout=20)
            root = ET.fromstring(body)
            items = root.findall("./channel/item")
            row.update(http_ok=True, xml_root=root.tag, bytes=len(body), items=len(items),
                       with_contributor=sum(1 for i in items if i.findtext(DC + "contributor")))
            matched = parse_wire_issuer_feed(body, contributor=entry["wire_contributor"], now=now, max_age_hours=24 * 365)
            matched_week = parse_wire_issuer_feed(body, contributor=entry["wire_contributor"], now=now)
            row.update(issuer_items_any_age=len(matched), issuer_items_7d=len(matched_week),
                       filtered_out=len(items) - len(matched),
                       sample_contributors=sorted({(i.findtext(DC + "contributor") or "")[:40] for i in items})[:5],
                       sample_issuer_titles=[m["title"][:90] for m in matched[:2]])
            payloads[entry["ticker"]] = (entry, body, matched_week)
        except Exception as exc:  # noqa: BLE001
            row.update(http_ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
        report["feeds"].append(row)

    with tempfile.TemporaryDirectory() as tmp:
        # Forward-only activation: first poll must record a baseline and emit no alerts from the backlog.
        conn = db.connect(Path(tmp) / "fwd.db")
        fwd = {}
        for ticker, (entry, body, matched) in payloads.items():
            first = _poll_wire_issuer(conn, ticker, entry, fetch=lambda _u, b=body: b, now=now)
            second = _poll_wire_issuer(conn, ticker, entry, fetch=lambda _u, b=body: b, now=now + timedelta(minutes=5))
            fwd[ticker] = {"first_signals": first["signals_seen"], "first_suppressed": first["baseline_suppressed"],
                           "second_signals": second["signals_seen"]}
        report["checks"]["forward_only"] = {
            "all_first_polls_emit_zero": all(v["first_signals"] == 0 for v in fwd.values()),
            "all_repeat_polls_emit_zero": all(v["second_signals"] == 0 for v in fwd.values()),
            "per_ticker": fwd}
        changes = conn.execute("SELECT count(*) FROM change_events").fetchone()[0]
        report["checks"]["forward_only"]["change_events_created"] = changes
        # Fail-closed on tampered evidence.
        tampered = False
        if payloads:
            t = next(iter(payloads))
            entry = payloads[t][0]
            observe(conn, "wire_issuer_baseline:globe_kw_v1:" + t,
                    {"transport_url": "https://example.com/x", "activated_at": now.isoformat()})
            try:
                _wire_issuer_baseline(conn, t, entry["wire_feed_url"], now)
            except ValueError:
                tampered = True
        report["checks"]["tampered_baseline_fails_closed"] = tampered

        # Dedup: categorical GlobeNewswire feeds and the issuer feeds both see the same release.
        conn2 = db.connect(Path(tmp) / "dedup.db")
        cat_payloads = {}
        for name, url in WIRE_FEEDS:
            if name.startswith("globenewswire"):
                try:
                    cat_payloads[url] = fetch_feed(url, timeout=20)
                except Exception as exc:  # noqa: BLE001
                    cat_payloads[url] = None
                    report["checks"].setdefault("categorical_errors", []).append(f"{name}: {exc}"[:150])
        cat_urls = set()
        for url, body in cat_payloads.items():
            if body:
                for item in ET.fromstring(body).findall("./channel/item"):
                    cat_urls.add(canonical_url((item.findtext("link") or "").strip()))
        overlap = {t: [m["url"] for m in matched if canonical_url(m["url"]) in cat_urls]
                   for t, (_e, _b, matched) in payloads.items()}
        report["checks"]["dedup_overlap_urls"] = sum(len(v) for v in overlap.values())
        # Back-date activation so that every item in the 7-day window counts as new, then run both lanes.
        backdated = (now - timedelta(days=8)).isoformat()
        for name, url in WIRE_FEEDS:
            if name.startswith("globenewswire"):
                observe(conn2, "wire_transport_baseline:globe_rss_v1:" + name,
                        {"transport_url": url, "activated_at": backdated, "policy": "forward_only_v1"},
                        source_url=url, source_type="wire")
        poll_wire_feeds(conn2, fetch=lambda u: cat_payloads[u] if cat_payloads.get(u) else fetch_feed(u, timeout=20), now=now)
        for ticker, (entry, body, _m) in payloads.items():
            observe(conn2, "wire_issuer_baseline:globe_kw_v1:" + ticker,
                    {"transport_url": entry["wire_feed_url"], "activated_at": backdated, "policy": "forward_only_v1"},
                    source_url=entry["wire_feed_url"], source_type="wire")
            _poll_wire_issuer(conn2, ticker, entry, fetch=lambda _u, b=body: b, now=now)
        rows = conn2.execute("SELECT source_url, change_type FROM change_events").fetchall()
        urls = [canonical_url(r[0]) for r in rows]
        dupes = sorted({u for u in urls if urls.count(u) > 1})
        report["checks"]["dedup"] = {"change_events": len(rows), "duplicate_urls": dupes,
                                     "ok": not dupes,
                                     "by_type": {t: sum(1 for r in rows if r[1] == t) for t in {r[1] for r in rows}}}

    # Synthetic-overlap dedup: real feeds rarely overlap at probe time, so replay one real issuer release
    # through BOTH lanes (categorical GlobeNewswire feed and issuer feed) and require a single alert.
    best = None
    for ticker, (entry, body, _m) in payloads.items():
        rows = parse_wire_issuer_feed(body, contributor=entry["wire_contributor"], now=now, max_age_hours=24 * 365)
        if rows:
            newest = max(rows, key=lambda r: r["published_at"])
            if best is None or newest["published_at"] > best[3]["published_at"]:
                best = (ticker, entry, body, newest)
    if best:
        ticker, entry, body, newest = best
        replay_now = datetime.fromisoformat(newest["published_at"]) + timedelta(hours=1)
        empty = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
        with tempfile.TemporaryDirectory() as tmp2:
            conn3 = db.connect(Path(tmp2) / "replay.db")
            back = (replay_now - timedelta(days=8)).isoformat()
            for name, url in WIRE_FEEDS:
                if name.startswith("globenewswire"):
                    observe(conn3, "wire_transport_baseline:globe_rss_v1:" + name,
                            {"transport_url": url, "activated_at": back, "policy": "forward_only_v1"},
                            source_url=url, source_type="wire")
            observe(conn3, "wire_issuer_baseline:globe_kw_v1:" + ticker,
                    {"transport_url": entry["wire_feed_url"], "activated_at": back, "policy": "forward_only_v1"},
                    source_url=entry["wire_feed_url"], source_type="wire")
            cat_url = dict(WIRE_FEEDS)["globenewswire_biotech"]
            poll_wire_feeds(conn3, fetch=lambda u: body if u == cat_url else empty, now=replay_now,
                            resolve_headline=lambda *_a, **_k: {"ticker": ticker, "source": "probe"})
            after_cat = conn3.execute("SELECT count(*) FROM change_events WHERE source_url=?", (newest["url"],)).fetchone()[0]
            res = _poll_wire_issuer(conn3, ticker, entry, fetch=lambda _u: body, now=replay_now)
            final = conn3.execute("SELECT count(*), group_concat(change_type) FROM change_events WHERE source_url=?",
                                  (newest["url"],)).fetchone()
            report["checks"]["synthetic_overlap_dedup"] = {
                "ticker": ticker, "url": newest["url"], "events_after_categorical_lane": after_cat,
                "events_after_issuer_lane": final[0], "types": final[1],
                "issuer_lane_signals_seen": res["signals_seen"], "ok": final[0] == 1}
    print(json.dumps(report, indent=2, ensure_ascii=False))
    bad = [f for f in report["feeds"] if not f.get("http_ok")]
    ok = (not bad and report["checks"]["forward_only"]["all_first_polls_emit_zero"]
          and report["checks"]["tampered_baseline_fails_closed"] and report["checks"]["dedup"]["ok"]
          and report["checks"].get("synthetic_overlap_dedup", {"ok": False})["ok"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
