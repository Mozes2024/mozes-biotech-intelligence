"""Every configured wire fallback must be complete enough to actually fire."""
import json
from pathlib import Path


def test_every_wire_feed_url_has_contributor_and_gnw_host():
    cfg = json.loads(Path("mozes/data/official_ir_sites.json").read_text())
    for ticker, entry in cfg.items():
        url = entry.get("wire_feed_url")
        if not url:
            continue
        assert url.startswith("https://rss.globenewswire.com/"), ticker
        assert (entry.get("wire_contributor") or "").strip(), (
            f"{ticker}: wire_feed_url without wire_contributor never fires "
            "(_poll_wire_issuer returns None)")
