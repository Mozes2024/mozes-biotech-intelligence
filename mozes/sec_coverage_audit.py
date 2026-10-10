"""Read-only SEC outage inventory. Never ingest, enqueue, publish or acknowledge."""
import hashlib
import json
import re
from datetime import date


def accession_key(value):
    return str(value).replace("-", "")


def stored_accessions(conn):
    known = {accession_key(r[0]) for r in conn.execute("SELECT accession FROM v2e2_filing_scans")}
    # Stored sources and observations are evidence of capture, not notification delivery.
    for table, columns in (("change_events", "new_value,metadata_json,source_url"),
                           ("monitor_observations", "observation_key,value_json,source_url")):
        for row in conn.execute(f"SELECT {columns} FROM {table}"):
            for value in row:
                known.update(accession_key(m) for m in re.findall(r"\b\d{10}-\d{2}-\d{6}\b", value or ""))
                known.update(re.findall(r"/([0-9]{18})/", value or ""))
                if value:
                    try:
                        payload = json.loads(value)
                        if isinstance(payload, dict) and payload.get("accession"):
                            known.add(accession_key(payload["accession"]))
                    except (ValueError, TypeError):
                        pass
    return known


def filing_rows(block, start, end):
    required = ("accessionNumber", "form", "filingDate")
    if any(not isinstance(block.get(k), list) for k in required):
        raise ValueError("SEC submissions missing required arrays")
    if len({len(block[k]) for k in required}) != 1:
        raise ValueError("SEC submissions arrays differ in length")
    # Filing dates intentionally include both boundary days; report a conservative superset.
    return [{"accession": acc, "form": form, "filed": filed}
            for acc, form, filed in zip(*(block[k] for k in required))
            if form in ("8-K", "6-K") and start <= date.fromisoformat(filed) <= end]


def audit_issuer(cik, issuer, *, fetch, start, end, known, max_archives=2):
    url = f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
    data = fetch(url)
    recent = data["filings"]["recent"]
    rows = filing_rows(recent, start, end)
    dates = [date.fromisoformat(x) for x in recent["filingDate"]]
    covers_start = bool(dates) and min(dates) <= start
    files = [] if covers_start else data["filings"]["files"]
    if not isinstance(files, list):
        raise ValueError("SEC archive catalog missing")
    selected = [f for f in files if date.fromisoformat(f["filingFrom"]) <= end
                and date.fromisoformat(f["filingTo"]) >= start]
    if len(selected) > max_archives:
        raise ValueError("SEC archive coverage exceeds bound")
    for archive in selected:
        name = archive["name"]
        if not re.fullmatch(r"CIK\d{10}-submissions-\d+\.json", name):
            raise ValueError("untrusted SEC archive name")
        rows.extend(filing_rows(fetch("https://data.sec.gov/submissions/" + name), start, end))
    unique = {accession_key(r["accession"]): r for r in rows}
    return {"cik": cik, "ticker": issuer["ticker"], "archives_read": len(selected),
            "filings": [{**row, "stored_capture": key in known} for key, row in unique.items()]}


def universe_digest(universe):
    return hashlib.sha256(json.dumps(universe, sort_keys=True).encode()).hexdigest()
