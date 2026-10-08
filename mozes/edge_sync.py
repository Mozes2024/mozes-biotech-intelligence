"""Bounded, authenticated issuer-universe snapshot for the Cloudflare detector."""
from __future__ import annotations

import json
import os
import urllib.request

from . import db
from .config import DB_PATH
from .ingest.edgar_latest import biotech_universe


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
    return {"issuers": rows}


def sync(conn, *, url=None, token=None, opener=None):
    url = url or os.environ.get("MOZES_EDGE_SYNC_URL")
    token = token or os.environ.get("EDGE_SYNC_TOKEN")
    if not url or not url.startswith("https://") or not token:
        return {"status": "SKIPPED", "reason": "edge sync URL/token unavailable"}
    payload = build_universe(conn)
    if not payload["issuers"]:
        return {"status": "SKIPPED", "reason": "verified universe empty"}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json"})
    with (opener or urllib.request.urlopen)(request, timeout=15) as response:
        if response.status != 200:
            raise OSError("edge sync failed")
    return {"status": "OK", "count": len(payload["issuers"])}


if __name__ == "__main__":
    connection = db.connect(os.environ.get("MOZES_DB_PATH", DB_PATH))
    try:
        print(json.dumps(sync(connection)))
    finally:
        connection.close()
