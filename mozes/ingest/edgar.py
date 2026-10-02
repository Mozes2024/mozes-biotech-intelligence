"""SEC EDGAR primary and EX-99 ingestion with bounded cached transport.
Requires an identifying SEC_USER_AGENT under the SEC fair-access policy."""
from __future__ import annotations

import html as _html
import json
import logging
import re
import time
import urllib.request
from datetime import date

from ..config import SEC_USER_AGENT
from ..extract import extract_catalyst_statements
from ..db import utcnow

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def _get(url, *, deadline=None):
    from ..sec_http import get_text
    return get_text(url, user_agent=SEC_USER_AGENT, deadline=deadline)


def _pause(seconds, deadline):
    if deadline is not None and time.monotonic() + seconds >= deadline:
        raise TimeoutError('SEC deep deadline exhausted before document wait')
    time.sleep(seconds)


def recent_filings(cik, forms=("8-K", "10-Q", "10-K"), limit=20):
    data = json.loads(_get(SUBMISSIONS.format(cik=int(cik))))
    rec = data["filings"]["recent"]
    out = []
    for i, form in enumerate(rec["form"]):
        if form in forms:
            acc = rec["accessionNumber"][i].replace("-", "")
            out.append({"form": form, "filed": rec["filingDate"][i],
                        "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{rec['primaryDocument'][i]}"})
        if len(out) >= limit:
            break
    return out


def html_to_text(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", h)
    return _html.unescape(re.sub(r"<[^>]+>", " ", h))


def extract_from_filing(filing):
    time.sleep(0.2)  # stay well below SEC rate limits
    text = html_to_text(_get(filing["url"]))
    return extract_catalyst_statements(text, date.fromisoformat(filing["filed"]), source_id=filing["url"], reliability="primary")

INDEX = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json"
TICKER_MAP = "https://www.sec.gov/files/company_tickers_exchange.json"


def recent_filings_v2(cik, forms=("8-K", "10-Q", "10-K", "6-K", "20-F"), limit=30, *, deadline=None):
    data = json.loads(_get(SUBMISSIONS.format(cik=int(cik)), deadline=deadline))
    rec = data["filings"]["recent"]
    out = []
    for i, form in enumerate(rec["form"]):
        if form not in forms:
            continue
        acc = rec["accessionNumber"][i].replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}"
        out.append({
            "cik": str(int(cik)), "form": form, "filed": rec["filingDate"][i], "accession": acc,
            "accepted": (rec.get("acceptanceDateTime") or [None] * len(rec["form"]))[i],
            "primary_document": rec["primaryDocument"][i],
            "url": f"{base}/{rec['primaryDocument'][i]}", "index_url": f"{base}/index.json",
        })
        if len(out) >= limit:
            break
    return out


def filing_documents(filing, return_diagnostics=False, *, deadline=None):
    """Return primary document plus likely press-release exhibits from filing index.json."""
    diagnostics = []
    try:
        idx = json.loads(_get(filing["index_url"], deadline=deadline))
    except Exception as exc:
        logging.getLogger(__name__).warning("SEC exhibit index unavailable: %s", str(exc))
        diagnostics.append({'status': 'INCOMPLETE', 'reason': 'index_unavailable',
                            'url': filing['index_url'], 'error': str(exc)[:240]})
        docs = [{"name": filing.get("primary_document"), "url": filing["url"], "kind": "primary"}]
        return (docs, diagnostics) if return_diagnostics else docs
    items = (idx.get("directory") or {}).get("item") or []
    diagnostics.append({'status': 'OK' if items else 'INCOMPLETE',
                        'reason': 'index_extracted' if items else 'index_empty',
                        'url': filing['index_url']})
    docs = []
    for it in items:
        name = (it.get("name") or "").strip()
        if not name or not re.search(r"\.(?:htm|html|txt)$", name, re.I):
            continue
        low = name.lower()
        kind = "primary" if name == filing.get("primary_document") else "other"
        if re.search(r"ex(?:hibit)?[-_]?99|ex99|99[-_.]?1", low):
            kind = "ex99"
        if kind in {"primary", "ex99"}:
            base = filing["url"].rsplit("/", 1)[0]
            docs.append({"name": name, "url": f"{base}/{name}", "kind": kind})
    # Primary first, exhibits second; deduplicate URLs.
    seen, out = set(), []
    for d in sorted(docs, key=lambda x: 0 if x["kind"] == "primary" else 1):
        if d["url"] not in seen:
            seen.add(d["url"]); out.append(d)
    docs = out or [{"name": filing.get("primary_document"), "url": filing["url"], "kind": "primary"}]
    return (docs, diagnostics) if return_diagnostics else docs


def extract_from_filing_v2(filing, return_diagnostics=False, *, deadline=None):
    """Extract dated catalyst statements from the filing and EX-99 exhibits."""
    rows, diagnostics = [], []
    docs, index_diagnostics = filing_documents(filing, return_diagnostics=True, deadline=deadline)
    diagnostics.extend(index_diagnostics)
    for doc in docs:
        _pause(0.12, deadline)
        try:
            text = html_to_text(_get(doc["url"], deadline=deadline))
        except Exception as exc:
            logging.getLogger(__name__).warning("SEC document unavailable %s: %s", doc["url"], str(exc))
            diagnostics.append({'status': 'INCOMPLETE', 'reason': 'document_unavailable', 'url': doc['url'], 'error': str(exc)[:240]})
            continue
        from ..intelligence_store import digest
        diagnostics.append({'status': 'OK', 'reason': 'extracted', 'url': doc['url'], 'source_hash': digest(text), 'document_kind': doc['kind']})
        for s in extract_catalyst_statements(text, date.fromisoformat(filing["filed"]), source_id=doc["url"], reliability="primary"):
            s["document_kind"] = doc["kind"]
            s["form"] = filing["form"]
            s["filed"] = filing["filed"]
            s["accepted"] = filing.get("accepted")
            s["accession"] = filing.get("accession")
            s["retrieved_at"] = utcnow()
            rows.append(s)
    return (rows, diagnostics) if return_diagnostics else rows


def fetch_company_ticker_map(*, deadline=None):
    """Official SEC ticker/CIK/name associations. SEC notes the map is periodically updated."""
    raw = json.loads(_get(TICKER_MAP, deadline=deadline))
    fields = raw.get("fields") or []
    return [dict(zip(fields, row)) for row in raw.get("data", [])]
