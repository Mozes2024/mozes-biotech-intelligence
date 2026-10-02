"""Live refresh pipeline: SEC company map, CT.gov discovery, SEC primary-source verification.

The pipeline is intentionally staged. Discovery never equals verification.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone, timedelta

from . import db
from .discovery import discover, store_candidates, study_to_candidate
from .ingest.edgar import fetch_company_ticker_map, recent_filings_v2, extract_from_filing_v2
from .promotion import promote_candidate
from .universe import normalize_org
from .source_observability import capture


def refresh_sec_company_map(conn, *, deadline=None):
    from .entity_resolution import update_company_map
    from .ingest.nasdaq_trader import fetch_current_listings
    result = update_company_map(conn, fetch_company_ticker_map(deadline=deadline),
                                fetch_current_listings(deadline=deadline))
    return len(result["projection"])


def _verified_subsidiary_maps(candidates, maps, *, deadline=None, limit=8):
    """Resolve close issuer-name variants only after an exact SEC Exhibit 21 match."""
    from .ingest.edgar import _get, html_to_text

    names = Counter()
    for candidate in candidates:
        if candidate.get('ticker'):
            continue
        names[candidate.get('sponsor') or ''] += 1
        names.update(c.get('name') or '' for c in candidate.get('collaborators') or [])
    parents_by_prefix = {}
    for parent in maps:
        norm = parent.get('sponsor_norm') or ''
        if parent.get('source') == 'SEC-v2C-equity' and len(norm) >= 6:
            parents_by_prefix.setdefault(norm[:6], []).append(parent)
    aliases = []
    for name, _ in names.most_common():
        if len(aliases) >= limit or (deadline is not None and time.monotonic() >= deadline):
            break
        norm = normalize_org(name)
        possible = [m for m in parents_by_prefix.get(norm[:6], [])
                    if norm.startswith(m['sponsor_norm'])
                    and 1 <= len(norm) - len(m['sponsor_norm']) <= 4
                    and ' ' not in norm[len(m['sponsor_norm']):]]
        if len({(m.get('cik'), m.get('ticker')) for m in possible}) != 1:
            continue
        parent = possible[0]
        if not parent.get('cik'):
            continue
        try:
            filings = recent_filings_v2(parent['cik'], forms=('10-K',), limit=1, deadline=deadline)
            if not filings:
                continue
            index = json.loads(_get(filings[0]['index_url'], deadline=deadline))
            documents = (index.get('directory') or {}).get('item') or []
            exhibit = next((d['name'] for d in documents
                            if re.search(r'(?:ex|exhibit)[-_ .]*21', d.get('name') or '', re.I)
                            and re.search(r'\.html?$', d.get('name') or '', re.I)), None)
            if not exhibit:
                continue
            url = filings[0]['url'].rsplit('/', 1)[0] + '/' + exhibit
            subsidiary_text = normalize_org(html_to_text(_get(url, deadline=deadline)))
            if not re.search(r'(?<![a-z0-9])' + re.escape(norm) + r'(?![a-z0-9])', subsidiary_text):
                continue
            aliases.append({**parent, 'sponsor': name, 'sponsor_norm': norm,
                            'confidence': min(float(parent['confidence']), .9),
                            'source': 'SEC-v2E-subsidiary|' + url})
        except (OSError, ValueError, KeyError, TimeoutError):
            continue
    return aliases


def discover_registry(conn, start=None, end=None, months=6, return_metadata=False, *, deadline=None):
    from .intelligence_store import ensure_schema, state_get, state_put
    ensure_schema(conn)
    maps = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map WHERE confidence>=0.85 AND (source NOT LIKE 'SEC %' OR source='SEC-v2C-equity')").fetchall()]
    rows, meta = discover(start=start, end=end, months=months, sponsor_map=maps,
                          return_metadata=True, deadline=deadline,
                          resume=state_get(conn, 'v2e2_ctgov_cursor'))
    if os.environ.get('SEC_USER_AGENT'):
        aliases = _verified_subsidiary_maps(rows, maps, deadline=deadline)
        if aliases:
            with conn:
                for alias in aliases:
                    conn.execute("INSERT OR IGNORE INTO sponsor_ticker_map(sponsor_norm,sponsor,ticker,cik,confidence,source,updated_at) VALUES(?,?,?,?,?,?,?)",
                                 (alias['sponsor_norm'], alias['sponsor'], alias['ticker'], alias['cik'],
                                  alias['confidence'], alias['source'], db.utcnow()))
            maps += aliases
            rows = [study_to_candidate(row['raw'], maps) if not row.get('ticker') else row for row in rows]
        meta['verified_subsidiary_aliases'] = len(aliases)
    store_candidates(conn, rows)
    state_put(conn, 'v2e2_ctgov_cursor', meta.get('resume_cursor'))
    return (rows, meta) if return_metadata else rows


def _candidates_for_ticker(conn, ticker):
    return [dict(r) for r in conn.execute(
        "SELECT d.* FROM discovery_candidates d LEFT JOIN event_state s ON s.event_id=d.promoted_event_id "
        "WHERE d.ticker=? AND (s.status IS NULL OR s.status IN ('VERIFIED','SCHEDULED','DISCOVERED','REVIEW_REQUIRED')) ORDER BY d.discovered_at DESC LIMIT 100", (ticker,)
    ).fetchall()]


def verify_candidates_from_sec(conn, filings_per_company=12, budget_seconds=120, *, deadline=None):
    """Scan recent filings/EX-99 for mapped CT.gov candidates and promote only primary-source matches."""
    maps = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map WHERE cik IS NOT NULL AND confidence>=0.85 AND (source NOT LIKE 'SEC %' OR source='SEC-v2C-equity')").fetchall()]
    by_ticker = {r["ticker"]: r for r in maps}
    now = db.utcnow()
    deadline = min(deadline, time.monotonic() + budget_seconds) if deadline is not None else time.monotonic() + budget_seconds
    from .intelligence_store import digest, ensure_schema, state_get, state_put
    ensure_schema(conn)
    # Observation time is not a material revision. Reconcile identities before
    # selecting the bounded work batch so invalid prefixes cannot occupy slots.
    grouped = conn.execute("SELECT ticker, MIN(discovered_at) first_seen, MAX(discovered_at) last_seen "
                           "FROM discovery_candidates WHERE ticker IS NOT NULL GROUP BY ticker").fetchall()
    for row in grouped:
        ticker = row['ticker']
        candidates = _candidates_for_ticker(conn, ticker)
        fingerprint = digest([{
            key: c.get(key) for key in ('candidate_id', 'nct_id', 'sponsor', 'ticker', 'phase', 'title',
                                        'primary_completion', 'last_update_posted', 'status')
        } for c in candidates])
        prior = conn.execute('SELECT material_fingerprint,mapping_generation,state FROM v2e2_issuer_queue WHERE ticker=?', (ticker,)).fetchone()
        changed = not prior or prior['material_fingerprint'] != fingerprint
        mapping = by_ticker.get(ticker)
        generation = digest([mapping.get('cik'), mapping.get('confidence'), mapping.get('source')]) if mapping else None
        repaired = bool(prior and prior['state'] == 'IDENTITY_REVIEW' and mapping)
        with conn:
            conn.execute("INSERT INTO v2e2_issuer_queue(ticker,first_seen,last_seen,material_fingerprint,material_changed_at,pending_material_at,mapping_generation,state) "
                         "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET last_seen=excluded.last_seen, "
                         "material_fingerprint=excluded.material_fingerprint, mapping_generation=excluded.mapping_generation, "
                         "material_changed_at=CASE WHEN ? THEN excluded.material_changed_at ELSE v2e2_issuer_queue.material_changed_at END, "
                         "pending_material_at=CASE WHEN ? THEN excluded.pending_material_at ELSE v2e2_issuer_queue.pending_material_at END, "
                         "completed_accessions=CASE WHEN ? THEN '[]' ELSE v2e2_issuer_queue.completed_accessions END, "
                         "next_eligible_at=CASE WHEN ? THEN NULL ELSE v2e2_issuer_queue.next_eligible_at END, "
                         "state=CASE WHEN excluded.state='IDENTITY_REVIEW' THEN 'IDENTITY_REVIEW' "
                         "WHEN ? THEN 'PENDING' ELSE v2e2_issuer_queue.state END",
                         (ticker, row['first_seen'], row['last_seen'], fingerprint, now if changed else None,
                          now if changed else None, generation, 'PENDING' if mapping else 'IDENTITY_REVIEW',
                          changed, changed, changed, changed or repaired, changed or repaired))
    all_rows = conn.execute('SELECT * FROM v2e2_issuer_queue').fetchall()
    identity_ineligible = 0
    eligible = []
    requested = 0
    for row in all_rows:
        ticker = row['ticker']
        candidates = _candidates_for_ticker(conn, ticker)
        if not candidates:
            continue  # terminal/empty clinical sets do not consume SEC slots
        requested += 1
        if ticker not in by_ticker:
            identity_ineligible += 1
            with conn:
                conn.execute("UPDATE v2e2_issuer_queue SET state='IDENTITY_REVIEW',failure_reason='missing_or_ambiguous_mapping' WHERE ticker=?", (ticker,))
            continue
        if row['state'] == 'IDENTITY_REVIEW':
            with conn:
                conn.execute("UPDATE v2e2_issuer_queue SET state='PENDING',next_eligible_at=NULL,failure_reason=NULL WHERE ticker=?", (ticker,))
        if row['state'] != 'BLOCKED' and (not row['next_eligible_at'] or row['next_eligible_at'] <= now):
            eligible.append(row)
    oldest = sorted(eligible, key=lambda r: (r['last_attempt'] is not None, r['last_attempt'] or r['first_seen'] or '', r['first_seen'] or '', r['ticker']))
    priority = sorted((r for r in eligible if r['pending_material_at']),
                      key=lambda r: (r['last_attempt'] is None, r['pending_material_at'], r['ticker']))[:10]
    tickers = [r['ticker'] for r in priority]
    tickers += [r['ticker'] for r in oldest if r['ticker'] not in tickers][:25 - len(tickers)]
    promoted, scanned, errors = [], 0, []
    incomplete_documents = []
    budget_exhausted = False
    attempted = completed = cached_filings = 0
    for ticker in tickers:
        if time.monotonic() >= deadline:
            budget_exhausted = True
            break
        m = by_ticker[ticker]
        candidates = _candidates_for_ticker(conn, ticker)
        ticker_incomplete = ticker_failed = ticker_deferred = False
        queue = conn.execute('SELECT completed_accessions FROM v2e2_issuer_queue WHERE ticker=?', (ticker,)).fetchone()
        completed_accessions = set(json.loads(queue['completed_accessions'] or '[]'))
        attempt_at = db.utcnow()
        attempted += 1
        with conn:
            conn.execute("UPDATE v2e2_issuer_queue SET last_attempt=?,state='RUNNING' WHERE ticker=?", (attempt_at, ticker))
        try:
            listing_key = 'v2e2_filings:' + ticker
            listing = state_get(conn, listing_key, {})
            if not isinstance(listing, dict):
                listing = {}
            try:
                listing_age = (datetime.now(timezone.utc) - datetime.fromisoformat(listing['at'])).total_seconds()
            except (KeyError, ValueError, TypeError):
                listing_age = float('inf')
            if (listing.get('cik') == m['cik'] and isinstance(listing.get('filings'), list)
                    and listing.get('limit', 0) >= filings_per_company
                    and 0 <= listing_age < 900):
                filings = listing['filings']
            else:
                filings = recent_filings_v2(m["cik"], limit=filings_per_company, deadline=deadline)
                state_put(conn, listing_key, {'cik': m['cik'], 'limit': filings_per_company,
                                              'at': db.utcnow(), 'filings': filings})
        except TimeoutError:
            budget_exhausted = ticker_deferred = True
            filings = []
        except Exception as exc:
            errors.append({"ticker": ticker, "error": str(exc)})
            with conn:
                conn.execute("UPDATE v2e2_issuer_queue SET consecutive_failures=consecutive_failures+1,failure_reason=?,next_eligible_at=?,pending_material_at=NULL,state='RETRY' WHERE ticker=?",
                             (str(exc)[:240], (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec='seconds'), ticker))
            continue
        for filing in filings:
            if time.monotonic() >= deadline:
                budget_exhausted = ticker_deferred = True
                break
            accession = filing.get('accession') or filing.get('url') or ''
            if accession in completed_accessions:
                continue
            scanned += 1
            try:
                prior_scan = conn.execute("SELECT statements_json,diagnostics_json FROM v2e2_filing_scans WHERE accession=? AND extraction_version='v2e2' AND status='COMPLETE'", (accession,)).fetchone()
                if prior_scan:
                    statements, scan_diagnostics = json.loads(prior_scan['statements_json']), json.loads(prior_scan['diagnostics_json'])
                    cached_filings += 1
                else:
                    statements, scan_diagnostics = extract_from_filing_v2(filing, return_diagnostics=True, deadline=deadline)
                from .intelligence_store import digest, encode
                for scan in scan_diagnostics:
                    ticker_incomplete = ticker_incomplete or scan.get('status') != 'OK'
                    scan_id = digest([filing.get('accession'), scan.get('url'), 'v2e2'])
                    with conn:
                        conn.execute("INSERT INTO v2e2_document_scans(scan_id,accession,document_url,source_hash,extraction_version,status,completeness,reason,scanned_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(accession,document_url,extraction_version) DO UPDATE SET source_hash=excluded.source_hash,status=excluded.status,completeness=excluded.completeness,reason=excluded.reason,scanned_at=excluded.scanned_at",
                                     (scan_id, accession, scan.get('url') or (filing.get('index_url') if scan.get('reason') == 'index_unavailable' else filing.get('url')), scan.get('source_hash'), 'v2e2', scan.get('status'), 'complete' if scan.get('status') == 'OK' else 'incomplete', scan.get('reason'), db.utcnow()))
                if not prior_scan:
                    with conn:
                        conn.execute("INSERT INTO v2e2_filing_scans(accession,extraction_version,statements_json,diagnostics_json,status,scanned_at) VALUES(?,'v2e2',?,?,?,?) "
                                     "ON CONFLICT(accession,extraction_version) DO UPDATE SET statements_json=excluded.statements_json,diagnostics_json=excluded.diagnostics_json,status=excluded.status,scanned_at=excluded.scanned_at",
                                     (accession, encode(statements), encode(scan_diagnostics),
                                      'INCOMPLETE' if not scan_diagnostics or any(s.get('status') != 'OK' for s in scan_diagnostics) else 'COMPLETE', db.utcnow()))
            except TimeoutError:
                budget_exhausted = ticker_deferred = True
                break
            except Exception as exc:
                errors.append({"ticker": ticker, "filing": filing.get("url"), "error": str(exc)})
                ticker_failed = True
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
            if scan_diagnostics and all(s.get('status') == 'OK' for s in scan_diagnostics):
                completed_accessions.add(accession)
                with conn:
                    conn.execute('UPDATE v2e2_issuer_queue SET completed_accessions=? WHERE ticker=?',
                                 (json.dumps(sorted(completed_accessions)), ticker))
            elif not scan_diagnostics:
                ticker_incomplete = True
        if not (ticker_incomplete or ticker_failed or ticker_deferred):
            from .live_monitor import observe
            observe(conn, "coverage_scan:" + ticker, db.utcnow(), source_type="sec")
        with conn:
            if ticker_deferred:
                conn.execute("UPDATE v2e2_issuer_queue SET failure_reason='deep_deadline',next_eligible_at=NULL,pending_material_at=NULL,state='INCOMPLETE' WHERE ticker=?", (ticker,))
            elif ticker_failed:
                conn.execute("UPDATE v2e2_issuer_queue SET failure_reason='filing_extraction_error',consecutive_failures=consecutive_failures+1,next_eligible_at=?,pending_material_at=NULL,state='RETRY' WHERE ticker=?",
                             ((datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec='seconds'), ticker))
            elif ticker_incomplete:
                incomplete_documents.append(ticker)
                conn.execute("UPDATE v2e2_issuer_queue SET failure_reason=?,consecutive_failures=consecutive_failures+1,next_eligible_at=?,pending_material_at=NULL,state='INCOMPLETE' WHERE ticker=?",
                             ('required_document_incomplete', (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec='seconds'), ticker))
            else:
                completed += 1
                conn.execute("UPDATE v2e2_issuer_queue SET last_success=?,failure_reason=NULL,consecutive_failures=0,next_eligible_at=NULL,pending_material_at=NULL,state='SUCCESS' WHERE ticker=?", (db.utcnow(), ticker))
        if ticker_deferred:
            break
    sync_watch_ciks(conn)
    return {"filings_scanned": scanned, "promoted": sorted(set(promoted)), "errors": errors,
            "incomplete_documents": incomplete_documents, "budget_exhausted": budget_exhausted,
            "requested": requested, "identity_ineligible": identity_ineligible,
            "selected": len(tickers), "attempted": attempted, "completed": completed,
            "deferred": max(0, requested - identity_ineligible - completed),
            "cached_filings": cached_filings}


def _record_decisions(conn, ticker, candidates, statement, outcome, reason):
    from .intelligence_store import digest, encode
    for candidate in candidates:
        decision_id = digest([ticker, candidate.get('candidate_id'), statement.get('source_id'), statement.get('statement'), outcome, reason])
        with conn:
            conn.execute("INSERT OR IGNORE INTO v2e2_promotion_decisions(decision_id,ticker,candidate_id,evidence_id,accession,outcome,reason,details,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (decision_id, ticker, candidate.get('candidate_id'), None, statement.get('accession'), outcome, reason, encode(statement), db.utcnow()))


def refresh_live(conn, start=None, end=None, months=6, do_sec_map=True, do_sec_verify=True,
                 *, budget_seconds=None, finalization_reserve_seconds=20):
    started = db.utcnow()
    started_monotonic = time.monotonic()
    work_deadline = (started_monotonic + max(0, budget_seconds - finalization_reserve_seconds)
                     if budget_seconds is not None else None)
    columns = {row[1] for row in conn.execute('PRAGMA table_info(refresh_runs)')}
    with conn:
        if 'owner_token' not in columns:
            conn.execute('ALTER TABLE refresh_runs ADD COLUMN owner_token TEXT')
            conn.execute('ALTER TABLE refresh_runs ADD COLUMN heartbeat_at TEXT')
        cur = conn.execute("INSERT INTO refresh_runs(started_at,status,details_json,owner_token,heartbeat_at) VALUES(?,?,?,?,?)",
                           (started, "RUNNING", "{}", os.environ.get('MOZES_RUN_TOKEN'), started))
        rid = cur.lastrowid
    details = {'budget_seconds': budget_seconds, 'finalization_reserve_seconds':
               finalization_reserve_seconds if budget_seconds is not None else None}
    metrics = None
    try:
        with capture() as metrics:
            from .security import audit_current_universe
            details["security_audit"] = audit_current_universe(conn, deadline=work_deadline)
            with conn:
                conn.execute('UPDATE refresh_runs SET heartbeat_at=? WHERE run_id=?', (db.utcnow(), rid))
            if not os.environ.get("SEC_USER_AGENT"):
                do_sec_map = do_sec_verify = False
                details["sec_skipped"] = "SEC_USER_AGENT not configured"
            if do_sec_map:
                details["sec_map_rows"] = refresh_sec_company_map(conn, deadline=work_deadline)
                details["watch_ciks_synced"] = sync_watch_ciks(conn)
            try:
                candidates, discovery_meta = discover_registry(conn, start=start, end=end, months=months,
                                                                 return_metadata=True, deadline=work_deadline)
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
                details["sec_verification"] = verify_candidates_from_sec(conn, deadline=work_deadline)
                details["sec_regulatory_discovery"] = scan_watch_universe_regulatory(conn, deadline=work_deadline)
            details["source_operations"] = metrics.snapshot()
            from .coverage import audit_coverage
            details["coverage_audit"] = audit_coverage(conn)
            status = "PARTIAL" if any(isinstance(v, dict) and v.get("errors") for v in details.values()) else "OK"
            if (details.get("sec_verification", {}).get("budget_exhausted") or details.get("sec_verification", {}).get("incomplete_documents")
                    or details.get("sec_regulatory_discovery", {}).get("budget_exhausted")):
                status = "INCOMPLETE"
            if discovery_meta.get("truncated"):
                status = "INCOMPLETE"
            if details.get("sec_skipped") and status == 'OK':
                status = "PARTIAL"
            if any(row["errors"] for row in details["source_operations"]["sources"].values()) and status == 'OK':
                status = "PARTIAL"
    except TimeoutError as exc:
        if metrics is not None:
            details["source_operations"] = metrics.snapshot()
        details['budget_stop_stage'] = str(exc)[:120]
        status = 'INCOMPLETE'
    except Exception as exc:
        if metrics is not None:
            details["source_operations"] = metrics.snapshot()
        details["fatal_error"] = str(exc)
        status = "FAILED"
    details['duration_seconds'] = round(time.monotonic() - started_monotonic, 3)
    verification = details.get('sec_verification') or {}
    regulatory = details.get('sec_regulatory_discovery') or {}
    if not verification:
        verification = {'completed': 0, 'deferred': conn.execute(
            'SELECT COUNT(DISTINCT ticker) FROM discovery_candidates WHERE ticker IS NOT NULL').fetchone()[0]}
    if not regulatory:
        regulatory = {'completed': 0, 'deferred': conn.execute(
            'SELECT COUNT(*) FROM watch_universe WHERE active=1 AND cik IS NOT NULL').fetchone()[0]}
    details['work_counts'] = {
        'verification_completed': verification.get('completed', 0),
        'verification_deferred': verification.get('deferred', 0),
        'regulatory_completed': regulatory.get('completed', 0),
        'regulatory_deferred': regulatory.get('deferred', 0),
    }
    details['stop_reason'] = ('deep_budget_exhausted' if work_deadline is not None and
                              time.monotonic() >= work_deadline else
                              'deferred_work' if status == 'INCOMPLETE' else
                              'error' if status in {'FAILED', 'PARTIAL'} else 'complete')
    if work_deadline is not None and time.monotonic() >= work_deadline and status != 'FAILED':
        status = 'INCOMPLETE'
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


def scan_watch_universe_regulatory(conn, filings_per_company=10, *, today=None, deadline=None):
    """SEC-first discovery for regulatory events that do not require a CT.gov candidate.

    Known application identities can revise their guidance. Unidentified applications
    are retained for review, not automatically verified or keyed by their date.
    """
    from .regulatory_lifecycle import apply_guidance, quarantine_unbound_auto_events
    quarantine_unbound_auto_events(conn)
    from .intelligence_store import ensure_schema, state_get, state_put
    ensure_schema(conn)
    promoted, scanned, errors, reviewed = [], 0, [], 0
    watches = [w for w in db.watch_rows(conn) if w.get('cik')]
    cursor = state_get(conn, 'v2e2_regulatory_cursor', '')
    progress = state_get(conn, 'v2e2_regulatory_progress', {})
    start = next((i for i, w in enumerate(watches) if w['ticker'] > cursor), 0)
    ordered = watches[start:] + watches[:start]
    attempted = 0
    budget_exhausted = False
    for w in ordered:
        if deadline is not None and time.monotonic() >= deadline:
            budget_exhausted = True
            break
        attempted += 1
        try:
            kwargs = {'forms': ("8-K", "6-K"), 'limit': filings_per_company}
            if deadline is not None:
                kwargs['deadline'] = deadline
            filings = recent_filings_v2(w["cik"], **kwargs)
        except TimeoutError:
            budget_exhausted = True
            break
        except Exception as exc:
            errors.append({"ticker": w["ticker"], "error": str(exc)})
            state_put(conn, 'v2e2_regulatory_cursor', w['ticker'])
            continue
        for filing in filings:
            if deadline is not None and time.monotonic() >= deadline:
                budget_exhausted = True
                break
            accession = filing.get('accession') or filing.get('url')
            if accession in progress.get(w['ticker'], []):
                continue
            scanned += 1
            try:
                statements = extract_from_filing_v2(filing, deadline=deadline) if deadline is not None else extract_from_filing_v2(filing)
            except TimeoutError:
                budget_exhausted = True
                break
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
            progress.setdefault(w['ticker'], []).append(accession)
            state_put(conn, 'v2e2_regulatory_progress', progress)
        if budget_exhausted:
            break
        progress.pop(w['ticker'], None)
        state_put(conn, 'v2e2_regulatory_progress', progress)
        state_put(conn, 'v2e2_regulatory_cursor', w['ticker'])
    return {"filings_scanned": scanned, "events": sorted(set(promoted)),
            "review_required": reviewed, "errors": errors, "requested": len(watches),
            "attempted": attempted, "completed": attempted - int(budget_exhausted),
            "deferred": max(0, len(watches) - attempted + int(budget_exhausted)),
            "budget_exhausted": budget_exhausted}
