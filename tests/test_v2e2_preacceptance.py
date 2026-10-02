"""Offline regressions for the v2E.2 pre-acceptance correction."""
import json
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest

from mozes import db, refresh
from mozes.intelligence_store import ensure_schema


def _database(tmp_path):
    conn = db.connect(tmp_path / 'preacceptance.db')
    ensure_schema(conn)
    return conn


def _candidate(conn, ticker, *, mapped=True):
    if mapped:
        conn.execute(
            'INSERT INTO sponsor_ticker_map(sponsor_norm,sponsor,ticker,cik,confidence,source,updated_at) '
            'VALUES(?,?,?,?,?,?,?)',
            (ticker.lower(), ticker, ticker, ticker.removeprefix('T') or '1', .9,
             'SEC-v2C-equity', db.utcnow()),
        )
    conn.execute(
        'INSERT INTO discovery_candidates(candidate_id,nct_id,sponsor,ticker,ticker_confidence,phase,title,'
        'primary_completion,last_update_posted,status,raw_json,discovered_at) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        ('C-' + ticker, 'NCT-' + ticker, ticker, ticker, .9, 'PHASE3', ticker,
         '2026-12', '2026-10-01', 'RECRUITING', '{}', db.utcnow()),
    )


def test_invalid_early_prefix_cannot_starve_late_valid_issuer(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    for i in range(100):
        _candidate(conn, f'AA{i:03d}', mapped=False)
    _candidate(conn, 'ZZZ')
    called = []
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda cik, **kw: called.append(cik) or [])
    result = refresh.verify_candidates_from_sec(conn)
    assert called == ['ZZZ']
    assert result['identity_ineligible'] == 100
    assert conn.execute("SELECT state FROM v2e2_issuer_queue WHERE ticker='AA000'").fetchone()[0] == 'IDENTITY_REVIEW'


def test_failing_first_batch_yields_to_waiting_issuer(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    for i in range(26):
        _candidate(conn, f'T{i:03d}')
    calls = []

    def provider(cik, **kw):
        calls.append(cik)
        if cik != '025':
            raise OSError('temporary')
        return []

    monkeypatch.setattr(refresh, 'recent_filings_v2', provider)
    refresh.verify_candidates_from_sec(conn)
    refresh.verify_candidates_from_sec(conn)
    assert '025' in calls


def test_partial_filing_deadline_never_marks_success_and_resumes(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    _candidate(conn, 'T001')
    filings = [{'accession': 'A1', 'url': 'https://www.sec.gov/a1'},
               {'accession': 'A2', 'url': 'https://www.sec.gov/a2'}]
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda *a, **kw: filings)
    fetched = []

    def extractor(filing, **kw):
        fetched.append(filing['accession'])
        return [], []

    monkeypatch.setattr(refresh, 'extract_from_filing_v2', extractor)
    original = refresh.time.monotonic
    ticks = iter([0, 0, 0, 121, 0, 0, 0, 0, 0])
    monkeypatch.setattr(refresh.time, 'monotonic', lambda: next(ticks, 0))
    first = refresh.verify_candidates_from_sec(conn, budget_seconds=120)
    monkeypatch.setattr(refresh.time, 'monotonic', original)
    assert first['budget_exhausted']
    assert conn.execute("SELECT state FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()[0] != 'SUCCESS'
    refresh.verify_candidates_from_sec(conn)
    assert fetched.count('A2') == 1


def test_extraction_failure_is_not_erased_by_later_success(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    _candidate(conn, 'T001')
    filings = [{'accession': 'A1', 'url': 'https://www.sec.gov/a1'},
               {'accession': 'A2', 'url': 'https://www.sec.gov/a2'}]
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda *a, **kw: filings)

    def extractor(filing, **kw):
        if filing['accession'] == 'A1':
            raise OSError('bad exhibit')
        return [], []

    monkeypatch.setattr(refresh, 'extract_from_filing_v2', extractor)
    refresh.verify_candidates_from_sec(conn)
    row = conn.execute("SELECT state,last_success FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()
    assert row['state'] == 'RETRY' and row['last_success'] is None


def test_sec_retry_respects_deadline_before_backoff(tmp_path):
    import urllib.error
    from mozes.sec_http import get_text

    current = [0.0]
    waits = []

    def sleeper(seconds):
        waits.append(seconds)
        current[0] += seconds

    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, 'busy', {}, None)

    with pytest.raises((TimeoutError, urllib.error.HTTPError)):
        get_text('https://www.sec.gov/test', user_agent='test contact@example.com',
                 cache_dir=tmp_path, opener=opener, clock=lambda: current[0],
                 sleeper=sleeper, deadline=.5, monotonic=lambda: current[0])
    assert current[0] <= .5


def test_regulatory_scan_stops_and_rotates(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    for ticker in ('AAA', 'BBB', 'CCC'):
        conn.execute('INSERT INTO watch_universe(ticker,cik,source,updated_at) VALUES(?,?,?,?)',
                     (ticker, ticker, 'test', db.utcnow()))
    monkeypatch.setattr('mozes.regulatory_lifecycle.quarantine_unbound_auto_events', lambda conn: None)
    calls = []
    current = [0]

    def provider(cik, **kw):
        calls.append(cik)
        current[0] += 1
        return []

    monkeypatch.setattr(refresh, 'recent_filings_v2', provider)
    monkeypatch.setattr(refresh.time, 'monotonic', lambda: current[0])
    first = refresh.scan_watch_universe_regulatory(conn, deadline=1.5)
    assert first['deferred'] > 0 and len(calls) < 3
    current[0] = 0
    refresh.scan_watch_universe_regulatory(conn, deadline=1.5)
    assert calls[0] != calls[2]


def test_regulatory_filing_progress_resumes_after_budget_stop(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    conn.execute('INSERT INTO watch_universe(ticker,cik,source,updated_at) VALUES(?,?,?,?)',
                 ('AAA', '1', 'test', db.utcnow()))
    monkeypatch.setattr('mozes.regulatory_lifecycle.quarantine_unbound_auto_events', lambda conn: None)
    filings = [{'accession': f'A{i}', 'url': f'https://www.sec.gov/{i}'} for i in range(3)]
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda *a, **kw: filings)
    current = [0.0]
    monkeypatch.setattr(refresh.time, 'monotonic', lambda: current[0])
    calls = []

    def extractor(filing, **kw):
        calls.append(filing['accession'])
        current[0] += 1
        return []

    monkeypatch.setattr(refresh, 'extract_from_filing_v2', extractor)
    first = refresh.scan_watch_universe_regulatory(conn, deadline=1.5)
    assert first['budget_exhausted'] and calls == ['A0', 'A1']
    current[0] = 0
    second = refresh.scan_watch_universe_regulatory(conn, deadline=1.5)
    assert not second['budget_exhausted'] and calls == ['A0', 'A1', 'A2']


def test_refresh_shares_deadline_and_persists_bounded_outcome(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    for ticker in ('AAA', 'BBB', 'CCC'):
        conn.execute('INSERT INTO watch_universe(ticker,cik,source,updated_at) VALUES(?,?,?,?)',
                     (ticker, ticker, 'test', db.utcnow()))
    monkeypatch.setenv('SEC_USER_AGENT', 'test contact@example.com')
    monkeypatch.setattr('mozes.security.audit_current_universe', lambda conn, **kw: {})
    monkeypatch.setattr('mozes.regulatory_lifecycle.quarantine_unbound_auto_events', lambda conn: None)
    monkeypatch.setattr('mozes.coverage.audit_coverage', lambda conn: {})
    monkeypatch.setattr(refresh, 'discover_registry', lambda conn, **kw: ([], {'truncated': False, 'errors': []}))
    deadlines = []
    monkeypatch.setattr(refresh, 'verify_candidates_from_sec', lambda conn, **kw:
                        deadlines.append(kw['deadline']) or {'budget_exhausted': False, 'errors': []})
    current = [0.0]
    monkeypatch.setattr(refresh.time, 'monotonic', lambda: current[0])

    def provider(cik, **kw):
        deadlines.append(kw['deadline'])
        current[0] += 1
        return []

    monkeypatch.setattr(refresh, 'recent_filings_v2', provider)
    result = refresh.refresh_live(conn, do_sec_map=False, budget_seconds=21.5)
    assert result['status'] == 'INCOMPLETE'
    assert set(deadlines) == {1.5}
    assert result['sec_regulatory_discovery']['deferred'] > 0
    assert result['duration_seconds'] < 21.5
    stored = conn.execute('SELECT status,details_json FROM refresh_runs WHERE run_id=?', (result['run_id'],)).fetchone()
    assert stored['status'] == 'INCOMPLETE'
    assert json.loads(stored['details_json'])['stop_reason'] == 'deep_budget_exhausted'


def test_ctgov_page_cursor_resumes_without_repeating_first_page():
    from mozes.discovery import discover
    seen = []

    def fetcher(url):
        seen.append(url)
        return {'studies': [], 'nextPageToken': 'second' if 'pageToken=' not in url else None}

    _, first = discover(start='2026-10-01', end='2026-12-31', lookback_days=0,
                        today=date(2026, 10, 1), fetcher=fetcher, max_pages=1,
                        return_metadata=True)
    assert first['resume_cursor']['page_token'] == 'second'
    _, second = discover(start='2026-10-01', end='2026-12-31', lookback_days=0,
                         today=date(2026, 10, 1), fetcher=fetcher, max_pages=1,
                         resume=first['resume_cursor'], return_metadata=True)
    assert 'pageToken=second' in seen[1]
    assert second['resume_cursor'] is None


def test_ctgov_inflight_timeout_keeps_page_continuation():
    from mozes.discovery import discover

    def fetcher(url):
        if 'pageToken=' in url:
            raise TimeoutError('network deadline')
        return {'studies': [], 'nextPageToken': 'second'}

    _, meta = discover(start='2026-10-01', end='2026-12-31', lookback_days=0,
                       today=date(2026, 10, 1), fetcher=fetcher,
                       return_metadata=True)
    assert meta['stop_reason'] == 'time_budget'
    assert meta['resume_cursor']['page_token'] == 'second'


@pytest.mark.parametrize('body', [None, [], 'text', 1])
def test_child_result_rejects_json_non_object(tmp_path, body):
    from mozes.pipeline_v2c import validate_child_result
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(body))
    assert validate_child_result(path, 'owner-a', 0)[0] is None


def test_v2_queue_migration_preserves_prior_success_and_adds_progress(tmp_path):
    path = tmp_path / 'v2.db'
    raw = sqlite3.connect(path)
    raw.executescript("CREATE TABLE v2c_state(key TEXT PRIMARY KEY,payload TEXT NOT NULL);"
                      "INSERT INTO v2c_state VALUES('schema_version','2');"
                      "CREATE TABLE v2e2_issuer_queue(ticker TEXT PRIMARY KEY,first_seen TEXT,last_seen TEXT,"
                      "material_fingerprint TEXT,material_changed_at TEXT,last_attempt TEXT,last_success TEXT,"
                      "failure_reason TEXT,consecutive_failures INTEGER NOT NULL DEFAULT 0,"
                      "next_eligible_at TEXT,mapping_generation TEXT,state TEXT NOT NULL DEFAULT 'PENDING');"
                      "INSERT INTO v2e2_issuer_queue(ticker,last_success,state) "
                      "VALUES('SLS','2026-10-01T00:00:00+00:00','SUCCESS');")
    raw.close()
    conn = db.connect(path)
    ensure_schema(conn)
    row = conn.execute("SELECT last_success,state,pending_material_at,completed_accessions "
                       "FROM v2e2_issuer_queue WHERE ticker='SLS'").fetchone()
    assert row['last_success'] == '2026-10-01T00:00:00+00:00'
    assert row['state'] == 'SUCCESS' and row['completed_accessions'] == '[]'


@pytest.mark.parametrize('mode', ['crash', 'timeout', 'malformed'])
def test_parent_finalizes_only_crashed_child_owner(tmp_path, monkeypatch, mode):
    from mozes import pipeline_v2c

    conn = _database(tmp_path)
    conn.execute('ALTER TABLE refresh_runs ADD COLUMN owner_token TEXT')
    conn.execute('ALTER TABLE refresh_runs ADD COLUMN heartbeat_at TEXT')
    other = conn.execute("INSERT INTO refresh_runs(started_at,status,owner_token) VALUES(?,'RUNNING','other')",
                         (db.utcnow(),)).lastrowid
    monkeypatch.setattr(pipeline_v2c, 'reconcile_catalog', lambda conn: {})
    monkeypatch.setattr(pipeline_v2c, 'import_local_research', lambda conn: {})
    monkeypatch.setattr(pipeline_v2c, 'identity_audit', lambda conn: {})
    monkeypatch.setattr('mozes.regulatory_lifecycle.quarantine_unbound_auto_events', lambda conn: {})
    monkeypatch.setattr('mozes.coverage.audit_coverage', lambda conn: {})
    monkeypatch.setattr('mozes.live_prices.refresh_live_prices', lambda conn: {'status': 'OK'})
    monkeypatch.setattr('mozes.live_monitor.run_monitor', lambda *a, **kw: {})
    monkeypatch.setattr('mozes.radar.live_event_records', lambda conn: [])
    monkeypatch.setattr('mozes.paper.PaperBook', lambda conn: SimpleNamespace(queue_candidates=lambda rows: 0))
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    monkeypatch.delenv('MOZES_RUN_TOKEN', raising=False)
    monkeypatch.setenv('MOZES_RESULT_PATH', str(tmp_path / 'child-result.json'))
    owners = []

    def crashed(cmd, **kw):
        owner = kw['env']['MOZES_RUN_TOKEN']
        owners.append(owner)
        conn.execute("INSERT INTO refresh_runs(started_at,status,owner_token) VALUES(?,'RUNNING',?)",
                     (db.utcnow(), owner))
        if mode == 'timeout':
            raise pipeline_v2c.subprocess.TimeoutExpired(cmd, 480)
        if mode == 'malformed':
            (tmp_path / 'child-result.json').write_text('[]')
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(pipeline_v2c.subprocess, 'run', crashed)
    pipeline_v2c.run_pipeline(conn, deep=True)
    pipeline_v2c.run_pipeline(conn, deep=True)
    assert owners[0] != owners[1]
    assert conn.execute("SELECT status FROM refresh_runs WHERE run_id=?", (other,)).fetchone()[0] == 'RUNNING'
    assert conn.execute("SELECT count(*) FROM refresh_runs WHERE owner_token IN (?,?) AND status='RUNNING'",
                        owners).fetchone()[0] == 0


def test_complete_filing_is_reused_for_unchanged_and_new_candidates(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    _candidate(conn, 'T001')
    filing = {'accession': 'A1', 'url': 'https://www.sec.gov/a1', 'index_url': 'https://www.sec.gov/index'}
    listing_calls = []
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda *a, **kw: listing_calls.append('list') or [filing])
    calls = []
    matches = []

    def extractor(filing, **kw):
        calls.append(filing['accession'])
        return [{'statement': 'NCT-NEW Phase 3 topline expected', 'source_id': filing['url'],
                 'source_url': filing['url'], 'accession': filing['accession']}], [
                    {'status': 'OK', 'url': filing['index_url'], 'reason': 'index_extracted'},
                    {'status': 'OK', 'url': filing['url'], 'reason': 'extracted'}]

    monkeypatch.setattr(refresh, 'extract_from_filing_v2', extractor)
    monkeypatch.setattr(refresh, 'promote_candidate', lambda conn, candidate, statement, ticker, **kw:
                        matches.append(candidate['candidate_id']) or None)
    refresh.verify_candidates_from_sec(conn)
    conn.execute("INSERT INTO discovery_candidates(candidate_id,nct_id,sponsor,ticker,ticker_confidence,phase,title,"
                 "primary_completion,last_update_posted,status,raw_json,discovered_at) "
                 "VALUES('C-T001-NEW','NCT-NEW','T001','T001',.9,'PHASE3','New program',"
                 "'2027-01','2026-10-02','RECRUITING','{}',?)", (db.utcnow(),))
    refresh.verify_candidates_from_sec(conn)
    refresh.verify_candidates_from_sec(conn)  # unchanged observation uses stored listing and extraction
    assert calls == ['A1']
    assert listing_calls == ['list']
    assert matches == ['C-T001-NEW']


def test_mapping_repair_and_unchanged_observation_do_not_reset_backoff(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    _candidate(conn, 'T001', mapped=False)
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda *a, **kw: [])
    refresh.verify_candidates_from_sec(conn)
    assert conn.execute("SELECT state FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()[0] == 'IDENTITY_REVIEW'
    conn.execute('INSERT INTO sponsor_ticker_map(sponsor_norm,sponsor,ticker,cik,confidence,source,updated_at) '
                 "VALUES('t001','T001','T001','1',.9,'manual',?)", (db.utcnow(),))
    refresh.verify_candidates_from_sec(conn)
    row = conn.execute("SELECT state,pending_material_at FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()
    assert row['state'] == 'SUCCESS' and row['pending_material_at'] is None
    future = '2099-01-01T00:00:00+00:00'
    conn.execute("UPDATE v2e2_issuer_queue SET state='RETRY',next_eligible_at=? WHERE ticker='T001'", (future,))
    conn.execute("UPDATE discovery_candidates SET raw_json='{\"observation\": 2}',discovered_at=? WHERE ticker='T001'", (db.utcnow(),))
    refresh.verify_candidates_from_sec(conn)
    row = conn.execute("SELECT state,next_eligible_at,pending_material_at FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()
    assert row['state'] == 'RETRY' and row['next_eligible_at'] == future and row['pending_material_at'] is None


def test_material_priority_does_not_starve_older_waiting_issuers(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    for i in range(40):
        _candidate(conn, f'T{i:03d}')
    called = []
    observed = []
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda cik, **kw: called.append(cik) or [])
    monkeypatch.setattr('mozes.live_monitor.observe', lambda conn, key, *a, **kw: observed.append(key))
    refresh.verify_candidates_from_sec(conn)
    called.clear()
    observed.clear()
    conn.execute("INSERT INTO discovery_candidates(candidate_id,nct_id,sponsor,ticker,ticker_confidence,phase,title,"
                 "primary_completion,last_update_posted,status,raw_json,discovered_at) "
                 "VALUES('C-MATERIAL','NCT-MATERIAL','T000','T000',.9,'PHASE3','material',"
                 "'2027-01','2026-10-02','RECRUITING','{}',?)", (db.utcnow(),))
    refresh.verify_candidates_from_sec(conn)
    assert 'coverage_scan:T000' in observed[:10]
    assert '025' in called


def test_partial_exhibit_fetch_remains_incomplete_and_retries(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    _candidate(conn, 'T001')
    filing = {'accession': 'A1', 'url': 'https://www.sec.gov/primary',
              'index_url': 'https://www.sec.gov/index'}
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda *a, **kw: [filing])
    calls = []

    def extractor(filing, **kw):
        calls.append(filing['accession'])
        diagnostics = [{'status': 'OK', 'url': filing['index_url'], 'reason': 'index_extracted'},
                       {'status': 'OK', 'url': filing['url'], 'reason': 'extracted'}]
        if len(calls) == 1:
            diagnostics.append({'status': 'INCOMPLETE', 'url': 'https://www.sec.gov/ex99',
                                'reason': 'document_unavailable'})
        return [], diagnostics

    monkeypatch.setattr(refresh, 'extract_from_filing_v2', extractor)
    refresh.verify_candidates_from_sec(conn)
    row = conn.execute("SELECT state,last_success FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()
    assert row['state'] == 'INCOMPLETE' and row['last_success'] is None
    conn.execute("UPDATE v2e2_issuer_queue SET next_eligible_at=NULL WHERE ticker='T001'")
    refresh.verify_candidates_from_sec(conn)
    assert calls == ['A1', 'A1']
    assert conn.execute("SELECT state FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()[0] == 'SUCCESS'


def test_index_failure_is_not_hidden_by_successful_primary(tmp_path, monkeypatch):
    conn = _database(tmp_path)
    _candidate(conn, 'T001')
    filing = {'accession': 'A1', 'url': 'https://www.sec.gov/primary',
              'index_url': 'https://www.sec.gov/index'}
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda *a, **kw: [filing])
    scans = [[{'status': 'INCOMPLETE', 'reason': 'index_unavailable'},
              {'status': 'OK', 'url': filing['url'], 'reason': 'extracted'}],
             [{'status': 'OK', 'url': filing['index_url'], 'reason': 'index_extracted'},
              {'status': 'OK', 'url': filing['url'], 'reason': 'extracted'}]]
    monkeypatch.setattr(refresh, 'extract_from_filing_v2', lambda *a, **kw: ([], scans.pop(0)))
    refresh.verify_candidates_from_sec(conn)
    row = conn.execute("SELECT status,document_url FROM v2e2_document_scans WHERE document_url=?",
                       (filing['index_url'],)).fetchone()
    assert row['status'] == 'INCOMPLETE'
    assert conn.execute("SELECT state FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()[0] == 'INCOMPLETE'
    conn.execute("UPDATE v2e2_issuer_queue SET next_eligible_at=NULL WHERE ticker='T001'")
    refresh.verify_candidates_from_sec(conn)
    assert conn.execute("SELECT state FROM v2e2_issuer_queue WHERE ticker='T001'").fetchone()[0] == 'SUCCESS'
