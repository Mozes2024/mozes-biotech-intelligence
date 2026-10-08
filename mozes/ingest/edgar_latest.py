"""SEC Latest Filings discovery. Per-CIK submissions remain reconciliation."""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode

from .. import db
from ..config import SEC_USER_AGENT
from ..live_monitor import observe, record_change
from ..materiality import classify_outcome
from ..sec_http import get_text
from .edgar import _get, filing_documents, html_to_text

ATOM = "{http://www.w3.org/2005/Atom}"
FEED = "https://www.sec.gov/cgi-bin/browse-edgar"


def feed_url(form):
    if form not in {"8-K", "6-K"}:
        raise ValueError(form)
    return FEED + "?" + urlencode({"action": "getcurrent", "type": form,
                                    "owner": "exclude", "count": 100, "output": "atom"})


def parse_atom(payload):
    root = ET.fromstring(payload)
    filings = []
    for entry in root.findall(ATOM + "entry"):
        title = entry.findtext(ATOM + "title") or ""
        match = re.match(r"^(8-K|6-K)\s+-\s+(.+?)\s+\((\d{1,10})\)\s+\(Filer\)", title)
        if not match:
            continue
        link = entry.find(ATOM + "link")
        url = link.get("href") if link is not None else None
        accession = re.search(r"accession-number=([\d-]+)", entry.findtext(ATOM + "id") or "")
        if not url or not accession:
            continue
        cik = str(int(match.group(3)))
        acc = accession.group(1)
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
        filings.append({"cik": cik, "company": match.group(2), "form": match.group(1),
                        "accession": acc, "accepted": entry.findtext(ATOM + "updated"),
                        "filing_index_url": url, "index_url": base + "/index.json",
                        "url": url, "filed": (entry.findtext(ATOM + "updated") or "")[:10]})
    return filings


def biotech_universe(conn):
    """Deduplicate CIKs for watched or active Phase 2/3 issuers."""
    tickers = {row[0] for row in conn.execute("SELECT ticker FROM watch_universe WHERE active=1")}
    tickers.update(row[0] for row in conn.execute(
        "SELECT ticker FROM discovery_candidates WHERE ticker IS NOT NULL "
        "AND (phase LIKE '%PHASE2%' OR phase LIKE '%PHASE3%') "
        "AND status IN ('RECRUITING','ACTIVE_NOT_RECRUITING','ENROLLING_BY_INVITATION','NOT_YET_RECRUITING')"))
    rows = conn.execute(
        "SELECT ticker,cik,sponsor AS company,confidence,source FROM sponsor_ticker_map "
        "WHERE cik IS NOT NULL AND confidence>=0.85 AND source='SEC-v2C-equity'").fetchall()
    identities = {}
    for row in rows:
        if row["ticker"] in tickers and str(row["cik"]).isdigit():
            identities.setdefault(str(int(row["cik"])), []).append(dict(row))
    universe = {cik: matches[0] for cik, matches in identities.items()
                if len({item["ticker"] for item in matches}) == 1}
    for row in conn.execute("SELECT ticker,cik,company,source FROM watch_universe WHERE active=1 AND cik IS NOT NULL"):
        if str(row["cik"]).isdigit():
            cik = str(int(row["cik"]))
            if cik not in identities:
                universe.setdefault(cik, dict(row))
    return universe


def poll_latest(conn, *, fetch=None, document_fetch=None, forms=("8-K", "6-K"), now=None):
    now = now or datetime.now(timezone.utc).isoformat()
    fetch = fetch or (lambda url: get_text(url, user_agent=SEC_USER_AGENT, ttl=0))
    document_fetch = document_fetch or _get
    universe = biotech_universe(conn)
    result = {"seen": 0, "material": 0, "errors": 0}
    for form in forms:
        url = feed_url(form)
        try:
            filings = parse_atom(fetch(url))
            observe(conn, "sec_latest:" + form, {"status": "active", "checked_at": now,
                                                   "items": len(filings)}, source_url=url, source_type="sec")
        except (OSError, ValueError, TypeError, ET.ParseError) as exc:
            result["errors"] += 1
            observe(conn, "sec_latest:" + form, {"status": "fetch_error", "checked_at": now,
                                                   "error": str(exc)[:160]}, source_url=url, source_type="sec")
            continue
        for filing in filings:
            issuer = universe.get(filing["cik"])
            if not issuer:
                continue
            key = "sec_latest_accession:" + filing["accession"]
            old = conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key=?", (key,)).fetchone()
            if old and json.loads(old[0]).get("status") == "complete":
                continue
            result["seen"] += 1
            observe(conn, key, {"status": "detected", "first_seen_at": now,
                                "accepted_at": filing["accepted"], "cik": filing["cik"]},
                    source_url=filing["filing_index_url"], source_type="sec")
            try:
                directory = json.loads(document_fetch(filing["index_url"]))["directory"]["item"]
                primary = next((item["name"] for item in directory
                                if re.search(r"\.(?:htm|html|txt)$", item.get("name", ""), re.I)
                                and "index" not in item["name"].lower()), None)
                if not primary:
                    raise ValueError("filing index has no primary document")
                filing["primary_document"] = primary
                filing["url"] = filing["index_url"].rsplit("/", 1)[0] + "/" + primary
                docs = filing_documents(filing)
                docs.sort(key=lambda doc: 0 if doc["kind"] == "ex99" else 1)
                selected = docs[0]
                text = html_to_text(document_fetch(selected["url"]))[:20000]
                outcome = classify_outcome(text)
                digest = hashlib.sha256(text.encode()).hexdigest()
                if outcome["material"]:
                    record_change(
                        conn, ticker=issuer["ticker"], change_type="sec_material_filing",
                        previous_value=None,
                        new_value={"form": form, "headline": text[:300], "accepted": filing["accepted"],
                                   "accession": filing["accession"], "outcome": outcome},
                        source_url=selected["url"], source_type="sec", severity="high",
                        verification_state="primary_source", source_hash=digest,
                        identity=["sec_latest", filing["accession"]],
                        metadata={"cik": filing["cik"], "filing_index_url": filing["filing_index_url"],
                                  "document_kind": selected["kind"], "accepted_at": filing["accepted"]},
                    )
                    result["material"] += 1
                observe(conn, key, {"status": "complete", "first_seen_at": now,
                                    "accepted_at": filing["accepted"], "cik": filing["cik"],
                                    "source_hash": digest, "material": outcome["material"]},
                        source_url=selected["url"], source_type="sec")
            except (OSError, ValueError, TypeError) as exc:
                result["errors"] += 1
                observe(conn, key, {"status": "fetch_error", "first_seen_at": now,
                                    "error": str(exc)[:160]},
                        source_url=filing["filing_index_url"], source_type="sec")
    return result
