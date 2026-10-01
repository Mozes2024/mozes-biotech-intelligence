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
from .source_observability import capture


def refresh_sec_company_map(conn):
    from .entity_resolution import update_company_map
    from .ingest.nasdaq_trader import fetch_current_listings
    result = update_company_map(conn, fetch_company_ticker_map(), fetch_current_listings())
    return len(result["projection"])


def discover_registry(conn, start=None, end=None, months=6, return_metadata=False):
    maps = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map WHERE confidence>=0.85 AND (source NOT LIKE 'SEC %' OR source='SEC-v2C-equity')").fetchall()]
    rows, meta = discover(start=start, end=end, months=months, sponsor_map=maps, return_metadata=True)
    store_candidates(conn, rows)
    return (rows, meta) if return_metadata else rows


def _candidates_for_ticker(conn, ticker):
    return [dict(r) for r in conn.execute(
        "SELECT d.* FROM discovery_candidates d LEFT JOIN event_state s ON s.event_id=d.promoted_event_id "
        "WHERE d.ticker=? AND (s.status IS NULL OR s.status IN ('VERIFIED','SCHEDULED','DISCOVERED','REVIEW_REQUIRED')) ORDER BY d.discovered_at DESC LIMIT 100", (ticker,)
    ).fetchall()]


def verify_candidates_from_sec(conn, filings_per_company=12):
    """Scan recent filings/EX-99 for mapped CT.gov candidates and promote only primary-source matches."""
    maps = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map WHERE cik IS NOT NULL AND confidence>=0.85 AND (source NOT LIKE 'SEC %' OR source='SEC-v2C-equity')").fetchall()]
    by_ticker = {r["ticker"]: r for r in maps}
    tickers = [r["ticker"] for r in conn.execute(
        "SELECT DISTINCT d.ticker,COALESCE(m.observed_at,'') last_check FROM discovery_candidates d "
        "LEFT JOIN monitor_observations m ON m.observation_key='coverage_scan:'||d.ticker "
        "WHERE d.ticker IS NOT NULL ORDER BY last_check,d.ticker LIMIT 25").fetchall()]
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
                from .intelligence_store import digest, encode
                from .promotion import candidate_matches_statement
                evidence_id = digest([ticker, stmt.get("source_url"), stmt.get("statement")])
                with conn:
                    conn.execute("INSERT OR IGNORE INTO catalyst_evidence VALUES(?,?,?,?,?,?)",
                                 (evidence_id, ticker, stmt.get("source_url") or stmt["source_id"],
                                  stmt.get("published_at") or stmt.get("filed"), db.utcnow(), encode(stmt)))
                matches = [cand for cand in candidates if candidate_matches_statement(cand, stmt.get("statement", ""))[0]]
                # One statement mentioning multiple studies cannot safely pick a study.
                if len({cand.get("nct_id") or cand["candidate_id"] for cand in matches}) != 1:
                    continue
                for cand in matches[:1]:
                    eid = promote_candidate(conn, cand, stmt, ticker, source_type="sec")
                    if eid:
                        promoted.append(eid)
        from .live_monitor import observe
        observe(conn, "coverage_scan:" + ticker, db.utcnow(), source_type="sec")
    return {"filings_scanned": scanned, "promoted": sorted(set(promoted)), "errors": errors}


def refresh_live(conn, start=None, end=None, months=6, do_sec_map=True, do_sec_verify=True):
    started = db.utcnow()
    with conn:
        cur = conn.execute("INSERT INTO refresh_runs(started_at,status,details_json) VALUES(?,?,?)", (started, "RUNNING", "{}"))
        rid = cur.lastrowid
    details = {}
    metrics = None
    try:
        with capture() as metrics:
            from .security import audit_current_universe
            details["security_audit"] = audit_current_universe(conn)
            if not os.environ.get("SEC_USER_AGENT"):
                do_sec_map = do_sec_verify = False
                details["sec_skipped"] = "SEC_USER_AGENT not configured"
            if do_sec_map:
                details["sec_map_rows"] = refresh_sec_company_map(conn)
                details["watch_ciks_synced"] = sync_watch_ciks(conn)
            candidates, discovery_meta = discover_registry(conn, start=start, end=end, months=months, return_metadata=True)
            details["ctgov_candidates"] = len(candidates)
            details["ctgov_mapped"] = sum(1 for x in candidates if x.get("ticker"))
            details["ctgov_discovery"] = discovery_meta
            if do_sec_verify:
                details["sec_verification"] = verify_candidates_from_sec(conn)
                details["sec_regulatory_discovery"] = scan_watch_universe_regulatory(conn)
            details["source_operations"] = metrics.snapshot()
            from .coverage import audit_coverage
            details["coverage_audit"] = audit_coverage(conn)
            status = "PARTIAL" if any(isinstance(v, dict) and v.get("errors") for v in details.values()) else "OK"
            if discovery_meta.get("truncated") and status == "OK":
                status = "INCOMPLETE"
            if details.get("sec_skipped"):
                status = "PARTIAL"
            if any(row["errors"] for row in details["source_operations"]["sources"].values()):
                status = "PARTIAL"
    except Exception as exc:
        if metrics is not None:
            details["source_operations"] = metrics.snapshot()
        details["fatal_error"] = str(exc)
        status = "FAILED"
    with conn:
        conn.execute("UPDATE refresh_runs SET finished_at=?,status=?,details_json=? WHERE run_id=?", (db.utcnow(), status, json.dumps(details), rid))
    return {"run_id": rid, "status": status, **details}


def sync_watch_ciks(conn):
    """Fill watch-universe CIK/company from the official SEC mapping already cached."""
    maps = {r["ticker"]: dict(r) for r in conn.execute("SELECT ticker,cik,sponsor FROM sponsor_ticker_map WHERE cik IS NOT NULL AND confidence>=0.85").fetchall()}
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


def scan_watch_universe_regulatory(conn, filings_per_company=10, *, today=None):
    """SEC-first discovery for regulatory events that do not require a CT.gov candidate.

    Known application identities can revise their guidance. Unidentified applications
    are retained for review, not automatically verified or keyed by their date.
    """
    from .regulatory_lifecycle import apply_guidance, quarantine_unbound_auto_events
    quarantine_unbound_auto_events(conn)
    promoted, scanned, errors, reviewed = [], 0, [], 0
    for w in db.watch_rows(conn):
        if not w.get("cik"):
            continue
        try:
            filings = recent_filings_v2(w["cik"], forms=("8-K", "6-K"), limit=filings_per_company)
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
                enriched = {**st, "form": filing.get("form", st.get("form")),
                            "filed": filing.get("filed", st.get("filed"))}
                result = apply_guidance(conn, w["ticker"], enriched, today=today)
                if result["status"] == "updated":
                    promoted.append(result["event_id"])
                elif result["status"] == "review":
                    reviewed += 1
    return {"filings_scanned": scanned, "events": sorted(set(promoted)),
            "review_required": reviewed, "errors": errors}
