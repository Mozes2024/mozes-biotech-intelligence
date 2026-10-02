import json
import sqlite3
from pathlib import Path

from mozes import db
from mozes.intelligence_store import ensure_schema, finalize_owned_runs, operation_start
from mozes.pipeline_v2c import validate_child_result
from mozes.discovery import store_candidates, study_to_candidate


def test_owned_timeout_finalization_does_not_touch_other_run(tmp_path):
    conn = db.connect(tmp_path / 'ownership.db')
    ensure_schema(conn)
    first = operation_start(conn, 'child', owner_token='owner-a')
    second = operation_start(conn, 'child', owner_token='owner-b')
    assert finalize_owned_runs(conn, 'owner-a', 'FAILED', 'parent_timeout') == 1
    rows = {row['run_id']: row for row in conn.execute('SELECT * FROM v2c_operation_runs')}
    assert rows[first]['status'] == 'FAILED' and rows[first]['stage'] == 'finalized'
    assert rows[second]['status'] == 'RUNNING'


def test_child_result_contract_rejects_stale_wrong_and_contradictory_results(tmp_path):
    path = Path(tmp_path / 'result.json')
    path.write_text(json.dumps({'protocol': 'mozes-v2e2-result', 'run_token': 'new', 'status': 'OK'}))
    assert validate_child_result(path, 'old', 0)[1] == 'wrong_result_owner_or_protocol'
    path.write_text(json.dumps({'protocol': 'mozes-v2e2-result', 'run_token': 'new', 'status': 'FAILED'}))
    assert validate_child_result(path, 'new', 0)[1] == 'contradictory_success_result'
    path.write_text('{broken')
    assert validate_child_result(path, 'new', 0)[1] == 'missing_or_malformed_result'


def test_incident_database_migration_is_additive(tmp_path):
    target = tmp_path / 'legacy-copy.db'
    raw = sqlite3.connect(target)
    raw.executescript("CREATE TABLE v2c_state(key TEXT PRIMARY KEY,payload TEXT NOT NULL); INSERT INTO v2c_state VALUES('schema_version','1');")
    raw.commit(); raw.close()
    conn = db.connect(target)
    ensure_schema(conn)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'v2e2_issuer_queue', 'v2e2_promotion_decisions', 'v2e2_document_scans'} <= names


def test_verification_queue_skips_unmapped_head_and_reaches_valid_late_issuer(tmp_path, monkeypatch):
    from mozes import refresh
    conn = db.connect(tmp_path / 'queue.db')
    ensure_schema(conn)
    maps = [{'sponsor_norm': 'sellas', 'sponsor': 'SELLAS Life Sciences Group', 'ticker': 'SLS', 'cik': '1390478',
             'confidence': 0.9, 'source': 'SEC-v2C-equity'}]
    for i in range(30):
        maps.append({'sponsor_norm': f'blocked{i}', 'sponsor': f'Blocked {i}', 'ticker': f'ZZ{i:02d}',
                     'cik': None, 'confidence': 0.9, 'source': 'manual'})
    for m in maps:
        conn.execute('INSERT INTO sponsor_ticker_map(sponsor_norm,sponsor,ticker,cik,confidence,source,updated_at) VALUES(?,?,?,?,?,?,?)',
                     (m['sponsor_norm'], m['sponsor'], m['ticker'], m['cik'], m['confidence'], m['source'], db.utcnow()))
    candidates = []
    for i, m in enumerate(maps):
        candidates.append(study_to_candidate({'NCTId': f'NCT{i:08d}', 'LeadSponsorName': m['sponsor'],
                                              'BriefTitle': f'Program {m["ticker"]}'}, maps))
    store_candidates(conn, candidates)
    called = []
    monkeypatch.setattr(refresh, 'recent_filings_v2', lambda cik, **kwargs: called.append(str(cik)) or [])
    out = refresh.verify_candidates_from_sec(conn)
    assert called == ['1390478']
    assert out['errors'] == []
    assert conn.execute("SELECT state FROM v2e2_issuer_queue WHERE ticker='SLS'").fetchone()[0] == 'SUCCESS'
