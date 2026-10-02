"""Live refresh pipeline: SEC company map, CT.gov discovery, SEC primary-source verification.

The pipeline is intentionally staged. Discovery never equals verification.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone, timedelta

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


def verify_candidates_from_sec(conn, filings_per_company=12, budget_seconds=120):
    """Scan recent filings/EX-99 for mapped CT.gov candidates and promote only primary-source matches."""
    maps = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map WHERE cik IS NOT NULL AND confidence>=0.85 AND (source NOT LIKE 'SEC %' OR source='SEC-v2C-equity')").fetchall()]
    by_ticker = {r["ticker"]: r for r in maps}
    now = db.utcnow()
    deadline = time.monotonic() + budget_seconds
    from .intelligence_store import digest, ensure_schema
    ensure_schema(conn)
    # Keep identity failures visible but out of the verification slots.  A
    # material revision resets backoff; an unchanged re-observation does not.
    grouped = conn.execute("SELECT ticker, MIN(discovered_at) first_seen, MAX(discovered_at) last_seen "
                           "FROM discovery_candidates WHERE ticker IS NOT NULL GROUP BY ticker").fetchall()
    for row in grouped:
        ticker = row['ticker']
        candidates = _candidates_for_ticker(conn, ticker)
        fingerprint = digest([{
            key: c.get(key) for key in ('candidate_id', 'nct_id', 'sponsor', 'ticker', 'phase', 'title',
                                        'primary_completion', 'last_update_posted', 'status', 'raw_json')
        } for c in candidates])
        prior = conn.execute('SELECT material_fingerprint FROM v2e2_issuer_queue WHERE ticker=?', (ticker,)).fetchone()
        changed = not prior or prior['material_fingerprint'] != fingerprint
        with conn:
            conn.execute("INSERT INTO v2e2_issuer_queue(ticker,first_seen,last_seen,material_fingerprint,material_changed_at,mapping_generation,state) "
                         "VALUES(?,?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET last_seen=excluded.last_seen, "
                         "material_fingerprint=excluded.material_fingerprint, material_changed_at=CASE WHEN excluded.material_changed_at IS NOT NULL THEN excluded.material_changed_at ELSE v2e2_issuer_queue.material_changed_at END, "
                         "state=CASE WHEN excluded.material_changed_at IS NOT NULL THEN 'PENDING' ELSE v2e2_issuer_queue.state END",
                         (ticker, row['first_seen'], row['last_seen'], fingerprint, now if changed else None,
                          digest(sorted((m['ticker'], m.get('cik'), m.get('confidence'), m.get('updated_at')) for m in by_ticker.values())), 'PENDING'))
    eligible = conn.execute("SELECT ticker FROM v2e2_issuer_queue WHERE (next_eligible_at IS NULL OR next_eligible_at<=?) "
                            "AND state NOT IN ('IDENTITY_REVIEW','BLOCKED') ORDER BY "
                            "CASE WHEN material_changed_at IS NOT NULL THEN 0 ELSE 1 END, "
                            "CASE WHEN last_attempt IS NULL THEN 0 ELSE 1 END, COALESCE(last_attempt,''), ticker LIMIT 100", (now,)).fetchall()
    # Only validated exact-symbol mappings enter the SEC verification slots.
    tickers = [r['ticker'] for r in eligible if r['ticker'] in by_ticker][:25]
    promoted, scanned, errors = [], 0, []
    incomplete_documents = []
    budget_exhausted = False
    for ticker in tickers:
        if time.monotonic() >= deadline:
            budget_exhausted = True
            break
        m = by_ticker.get(ticker)
        if not m:
            with conn:
                conn.execute("UPDATE v2e2_issuer_queue SET state='IDENTITY_REVIEW',last_attempt=?,failure_reason=? WHERE ticker=?", (now, 'missing_or_ambiguous_mapping', ticker))
            continue
        candidates = _candidates_for_ticker(conn, ticker)
        ticker_incomplete = False
        attempt_at = db.utcnow()
        with conn:
            conn.execute("UPDATE v2e2_issuer_queue SET last_attempt=?,state='RUNNING' WHERE ticker=?", (attempt_at, ticker))
        try:
            filings = recent_filings_v2(m["cik"], limit=filings_per_company)
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
            with conn:
                conn.execute("UPDATE v2e2_issuer_queue SET last_success=NULL,consecutive_failures=consecutive_failures+1,failure_reason=?,next_eligible_at=?,state='RETRY' WHERE ticker=?",
                             (str(exc)[:240], (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec='seconds'), ticker))
            continue
        for filing in filings:
            if time.monotonic() >= deadline:
                budget_exhausted = True
                break
            scanned += 1
            try:
                try:
                    statements, scan_diagnostics = extract_from_filing_v2(filing, return_diagnostics=True)
                except TypeError:
                    statements, scan_diagnostics = extract_from_filing_v2(filing), []
                from .intelligence_store import digest, encode
                for scan in scan_diagnostics:
                    ticker_incomplete = ticker_incomplete or scan.get('status') != 'OK'
                    scan_id = digest([filing.get('accession'), scan.get('url'), 'v2e2'])
                    with conn:
                        conn.execute("INSERT INTO v2e2_document_scans(scan_id,accession,document_url,source_hash,extraction_version,status,completeness,reason,scanned_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(accession,document_url,extraction_version) DO UPDATE SET source_hash=excluded.source_hash,status=excluded.status,completeness=excluded.completeness,reason=excluded.reason,scanned_at=excluded.scanned_at",
                                     (scan_id, filing.get('accession') or '', scan.get('url') or filing.get('url'), scan.get('source_hash'), 'v2e2', scan.get('status'), 'complete' if scan.get('status') == 'OK' else 'incomplete', scan.get('reason'), db.utcnow()))
            except Exception as exc:
                errors.append({"ticker": ticker, "filing": filing.get("url"), "error": str(exc)})
                with conn:
                    conn.execute("UPDATE v2e2_issuer_queue SET failure_reason=?,consecutive_failures=consecutive_failures+1,state='RETRY' WHERE ticker=?", (str(exc)[:240], ticker))
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
                if not matches:
                    _record_decisions(conn, ticker, candidates, stmt, 'NO_MATCH', 'no_candidate_match')
                # One statement mentioning multiple studies cannot safely pick a study.
                if len({cand.get("nct_id") or cand["candidate_id"] for cand in matches}) != 1:
                    _record_decisions(conn, ticker, matches, stmt, 'AMBIGUOUS', 'multiple_candidate_matches')
                    continue
                for cand in matches[:1]:
                    eid = promote_candidate(conn, cand, stmt, ticker, source_type="sec")
                    if eid:
                        promoted.append(eid)
        from .live_monitor import observe
        observe(conn, "coverage_scan:" + ticker, db.utcnow(), source_type="sec")
        with conn:
            if ticker_incomplete:
                incomplete_documents.append(ticker)
                conn.execute("UPDATE v2e2_issuer_queue SET last_success=?,failure_reason=?,consecutive_failures=consecutive_failures+1,next_eligible_at=?,state='INCOMPLETE' WHERE ticker=?",
                             (db.utcnow(), 'required_document_incomplete', (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec='seconds'), ticker))
            else:
                conn.execute("UPDATE v2e2_issuer_queue SET last_success=?,failure_reason=NULL,consecutive_failures=0,next_eligible_at=NULL,state='SUCCESS' WHERE ticker=?", (db.utcnow(), ticker))
    sync_watch_ciks(conn)
    return {"filings_scanned": scanned, "promoted": sorted(set(promoted)), "errors": errors,
            "incomplete_documents": incomplete_documents, "budget_exhausted": budget_exhausted}


def _record_decisions(conn, ticker, candidates, statement, outcome, reason):
    from .intelligence_store import digest, encode
    for candidate in candidates:
        decision_id = digest([ticker, candidate.get('candidate_id'), statement.get('source_id'), statement.get('statement'), outcome, reason])
        with conn:
            conn.execute("INSERT OR IGNORE INTO v2e2_promotion_decisions(decision_id,ticker,candidate_id,evidence_id,accession,outcome,reason,details,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (decision_id, ticker, candidate.get('candidate_id'), None, statement.get('accession'), outcome, reason, encode(statement), db.utcnow()))


def refresh_live(conn, start=None, end=None, months=6, do_sec_map=True, do_sec_verify=True):
    started = db.utcnow()
    columns = {row[1] for row in conn.execute('PRAGMA table_info(refresh_runs)')}
    with conn:
        if 'owner_token' not in columns:
            conn.execute('ALTER TABLE refresh_runs ADD COLUMN owner_token TEXT')
            conn.execute('ALTER TABLE refresh_runs ADD COLUMN heartbeat_at TEXT')
        cur = conn.execute("INSERT INTO refresh_runs(started_at,status,details_json,owner_token,heartbeat_at) VALUES(?,?,?,?,?)",
                           (started, "RUNNING", "{}", os.environ.get('MOZES_RUN_TOKEN'), started))
        rid = cur.lastrowid
    details = {}
    metrics = None
    try:
        with capture() as metrics:
            from .security import audit_current_universe
            details["security_audit"] = audit_current_universe(conn)
            with conn:
                conn.execute('UPDATE refresh_runs SET heartbeat_at=? WHERE run_id=?', (db.utcnow(), rid))
            if not os.environ.get("SEC_USER_AGENT"):
                do_sec_map = do_sec_verify = False
                details["sec_skipped"] = "SEC_USER_AGENT not configured"
            if do_sec_map:
                details["sec_map_rows"] = refresh_sec_company_map(conn)
                details["watch_ciks_synced"] = sync_watch_ciks(conn)
            try:
                candidates, discovery_meta = discover_registry(conn, start=start, end=end, months=months, return_metadata=True)
            except Exception as exc:
                # Preserve already stored candidates so SEC verification can continue
                # even when this pass cannot reach the registry.
                candidates = []
                discovery_meta = {'pages_fetched': 0, 'candidates_seen': 0, 'candidates_kept': 0,
                                  'truncated': False, 'stop_reason': 'source_error',
                                  'next_page_token_present': False, 'duration_ms': 0,
                                  'errors': [str(exc)[:240]]}
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
            if (details.get("sec_verification", {}).get("budget_exhausted") or details.get("sec_verification", {}).get("incomplete_documents")) and status == "OK":
                status = "INCOMPLETE"
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
        conn.execute("UPDATE refresh_runs SET finished_at=?,heartbeat_at=?,status=?,details_json=? WHERE run_id=?", (db.utcnow(), db.utcnow(), status, json.dumps(details), rid))
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
