"""Bounded, authenticated issuer-universe snapshot for the Cloudflare detector."""
from __future__ import annotations

import json
import os
import urllib.request

from . import db
from .config import DB_PATH
from .ingest.edgar_latest import biotech_universe
from .live_monitor import observe


def build_universe(conn):
    rows = []
    for cik, item in biotech_universe(conn).items():
        company = item.get("company") or ""
        if not company or len(company) < 3:
            continue
        rows.append({"cik": cik, "ticker": item["ticker"], "company": company[:200],
                     "confidence": float(item.get("confidence") or 0.85),
                     "source": item.get("source") or "watch_universe"})
    if len(rows) > 5000:
        raise ValueError("edge issuer universe exceeds bounded sync limit")
    return {"issuers": sorted(rows, key=lambda row: (str(int(row["cik"])), row["ticker"]))}


def sync(conn, *, url=None, token=None, opener=None):
    url = url or os.environ.get("MOZES_EDGE_SYNC_URL")
    token = token or os.environ.get("EDGE_SYNC_TOKEN")
    if not url and not token:
        return {"status": "SKIPPED", "reason": "edge sync not configured"}
    if not url or not url.startswith("https://") or not token:
        raise RuntimeError("edge sync configuration incomplete")
    payload = build_universe(conn)
    if not payload["issuers"]:
        raise RuntimeError("verified edge issuer universe empty")
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json",
                                              "User-Agent": "MOZES-EdgeSync/1.0"})
    with (opener or urllib.request.urlopen)(request, timeout=15) as response:
        if response.status != 200:
            raise OSError("edge sync failed")
    return {"status": "OK", "count": len(payload["issuers"])}


def capture_health(conn, *, opener=urllib.request.urlopen):
    url = os.environ.get("MOZES_EDGE_SYNC_URL")
    if not url:
        return
    health_url = url.rsplit("/", 1)[0] + "/health"
    try:
        request = urllib.request.Request(health_url, headers={"User-Agent": "MOZES-EdgeSync/1.0"})
        with opener(request, timeout=15) as response:
            health = json.loads(response.read(131072))
        health["snapshot_at"] = db.utcnow()
    except (OSError, ValueError) as exc:
        health = {"status": "FAILED", "snapshot_at": db.utcnow(), "error": type(exc).__name__}
    observe(conn, "edge_health", health, source_url=health_url, source_type="edge")


if __name__ == "__main__":
    connection = db.connect(os.environ.get("MOZES_DB_PATH", DB_PATH))
    try:
        print(json.dumps(sync(connection)))
        capture_health(connection)
    finally:
        connection.close()
