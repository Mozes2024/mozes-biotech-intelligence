"""Live refresh pipeline: SEC company map, CT.gov discovery, SEC primary-source verification.

The pipeline is intentionally staged. Discovery never equals verification.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from . import db
from .discovery import discover, store_candidates
from .ingest.edgar import fetch_company_ticker_map, recent_filings_v2, extract_from_filing_v2
from .promotion import promote_candidate
from .universe import normalize_org
from .session import session_from_sec_acceptance


def refresh_sec_company_map(conn):
    rows = fetch_company_ticker_map()
    now = db.utcnow()
    n = 0
    with conn:
        for r in rows:
            name = r.get("name") or r.get("title") or r.get("company")
            ticker = r.get("ticker")
            cik = r.get("cik") or r.get("cik_str")
            if not name or not ticker or cik is None:
                continue
            norm = normalize_org(name)
            if not norm:
                continue
            conn.execute(
                "INSERT INTO sponsor_ticker_map(sponsor_norm,sponsor,ticker,cik,confidence,source,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(sponsor_norm) DO UPDATE SET sponsor=excluded.sponsor,ticker=excluded.ticker,cik=excluded.cik,confidence=excluded.confidence,source=excluded.source,updated_at=excluded.updated_at",
                (norm, name, ticker, str(cik), 0.90, "SEC company_tickers_exchange.json", now),
            )
            n += 1
    return n


def discover_registry(conn, start=None, end=None, months=6):
    maps = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map").fetchall()]
    rows = discover(start=start, end=end, months=months, sponsor_map=maps)
    store_candidates(conn, rows)
    return rows


def _candidates_for_ticker(conn, ticker):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM discovery_candidates WHERE ticker=? AND promoted_event_id IS NULL ORDER BY primary_completion", (ticker,)
    ).fetchall()]


def verify_candidates_from_sec(conn, filings_per_company=12):
    """Scan recent filings/EX-99 for mapped CT.gov candidates and promote only primary-source matches."""
    maps = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map WHERE cik IS NOT NULL").fetchall()]
    by_ticker = {r["ticker"]: r for r in maps}
    tickers = [r["ticker"] for r in conn.execute("SELECT DISTINCT ticker FROM discovery_candidates WHERE ticker IS NOT NULL AND promoted_event_id IS NULL").fetchall()]
    promoted, scanned, errors = [], 0, []
    for ticker in tickers:
        m = by_ticker.get(ticker)
        if not m:
            continue
        candidates = _candidates_for_ticker(conn, ticker)
        try:
            filings = recent_filings_v2(m["cik"], limit=filings_per_company)
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
            continue
        for filing in filings:
            scanned += 1
            try:
                statements = extract_from_filing_v2(filing)
            except Exception as exc:
                errors.append({"ticker": ticker, "filing": filing.get("url"), "error": str(exc)})
                continue
            for stmt in statements:
                for cand in candidates:
                    eid = promote_candidate(conn, cand, stmt, ticker, source_type="sec")
                    if eid:
                        promoted.append(eid)
                        candidates = [c for c in candidates if c["candidate_id"] != cand["candidate_id"]]
    return {"filings_scanned": scanned, "promoted": sorted(set(promoted)), "errors": errors}


def refresh_live(conn, start=None, end=None, months=6, do_sec_map=True, do_sec_verify=True):
    started = db.utcnow()
    with conn:
        cur = conn.execute("INSERT INTO refresh_runs(started_at,status,details_json) VALUES(?,?,?)", (started, "RUNNING", "{}"))
        rid = cur.lastrowid
    details = {}
    try:
        from .security import audit_current_universe
        details["security_audit"] = audit_current_universe(conn)
        if not os.environ.get("SEC_USER_AGENT"):
            do_sec_map = do_sec_verify = False
            details["sec_skipped"] = "SEC_USER_AGENT not configured"
        if do_sec_map:
            details["sec_map_rows"] = refresh_sec_company_map(conn)
            details["watch_ciks_synced"] = sync_watch_ciks(conn)
        candidates = discover_registry(conn, start=start, end=end, months=months)
        details["ctgov_candidates"] = len(candidates)
        details["ctgov_mapped"] = sum(1 for x in candidates if x.get("ticker"))
        if do_sec_verify:
            details["sec_verification"] = verify_candidates_from_sec(conn)
            details["sec_regulatory_discovery"] = scan_watch_universe_regulatory(conn)
        status = "OK"
    except Exception as exc:
        details["fatal_error"] = str(exc)
        status = "FAILED"
    with conn:
        conn.execute("UPDATE refresh_runs SET finished_at=?,status=?,details_json=? WHERE run_id=?", (db.utcnow(), status, json.dumps(details), rid))
    return {"run_id": rid, "status": status, **details}


def sync_watch_ciks(conn):
    """Fill watch-universe CIK/company from the official SEC mapping already cached."""
    maps = {r["ticker"]: dict(r) for r in conn.execute("SELECT ticker,cik,sponsor FROM sponsor_ticker_map WHERE cik IS NOT NULL").fetchall()}
    n = 0
    with conn:
        for w in db.watch_rows(conn):
            m = maps.get(w["ticker"])
            if not m:
                continue
            conn.execute("UPDATE watch_universe SET cik=?,company=COALESCE(company,?),updated_at=? WHERE ticker=?",
                         (m["cik"], m.get("sponsor"), db.utcnow(), w["ticker"]))
            n += 1
    return n


def _reg_event_key(ticker, kind, window):
    import hashlib
    raw = f"{ticker}|{kind}|{window.get('start')}|{window.get('end')}".encode()
    return "AUTO-REG-" + hashlib.sha1(raw).hexdigest()[:16]


def scan_watch_universe_regulatory(conn, filings_per_company=10):
    """SEC-first discovery for regulatory events that do not require a CT.gov candidate.

    To control false positives, auto-creation is limited to PDUFA/target-action and AdCom
    statements with a parsed date window. Clinical readouts still require candidate matching.
    """
    promoted, scanned, errors = [], 0, []
    for w in db.watch_rows(conn):
        if not w.get("cik"):
            continue
        try:
            filings = recent_filings_v2(w["cik"], limit=filings_per_company)
        except Exception as exc:
            errors.append({"ticker": w["ticker"], "error": str(exc)})
            continue
        for filing in filings:
            scanned += 1
            try:
                statements = extract_from_filing_v2(filing)
            except Exception as exc:
                errors.append({"ticker": w["ticker"], "filing": filing.get("url"), "error": str(exc)})
                continue
            for st in statements:
                raw_type = st.get("catalyst_type")
                if raw_type not in {"PDUFA", "ADCOM"}:
                    continue
                win = st.get("window") or {}
                if win.get("precision") == "unknown" or not win.get("start"):
                    continue
                etype = "PDUFA_GENERIC" if raw_type == "PDUFA" else "ADCOM"
                eid = _reg_event_key(w["ticker"], etype, win)
                event = {
                    "id": eid, "ticker": w["ticker"], "company": w.get("company"),
                    "program": st.get("statement", "")[:220], "indication": None, "ta": None,
                    "type": etype, "phase": "regulatory", "pivotal": False, "mcap": "unknown",
                    "dependency": None, "commercial": False, "enables_filing": True,
                    "features": {}, "features_as_of": None, "documents": [], "flags": [], "prices": [],
                    "chronology": [{"date": st.get("filed"), "date_text": win.get("original"), "src": st.get("source_id"), "text": st.get("statement")}],
                    "auto_discovered": True,
                }
                with conn:
                    conn.execute("INSERT OR IGNORE INTO events(id,kind,payload) VALUES(?,?,?)", (eid, "live", json.dumps(event, ensure_ascii=False)))
                db.add_event_source(conn, eid, st.get("source_id") or eid, "sec", st.get("source_id"), st.get("filed"), st.get("statement"), True)
                db.upsert_event_state(conn, eid, status="SCHEDULED" if win.get("precision") == "exact" else "VERIFIED",
                                      verification_state="VERIFIED", verification_confidence=100 if win.get("precision") == "exact" else 95,
                                      event_timestamp=st.get("accepted"), event_session=session_from_sec_acceptance(st.get("accepted")),
                                      note="SEC-first regulatory discovery from filing/EX-99")
                promoted.append(eid)
    return {"filings_scanned": scanned, "events": sorted(set(promoted)), "errors": errors}
