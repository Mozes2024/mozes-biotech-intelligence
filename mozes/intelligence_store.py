"""Additive v2C storage. No writes to historical cases, labels or validation gates."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

DDL = """
CREATE TABLE IF NOT EXISTS v2c_financial_snapshots (
 snapshot_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, cik TEXT NOT NULL,
 as_of TEXT NOT NULL, captured_at TEXT NOT NULL, source_hash TEXT NOT NULL,
 source_url TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2c_financial_lookup ON v2c_financial_snapshots(ticker,as_of);
CREATE TABLE IF NOT EXISTS v2c_financing_events (
 record_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, accession TEXT NOT NULL,
 published_at TEXT NOT NULL, captured_at TEXT NOT NULL, source_url TEXT NOT NULL,
 source_hash TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2c_security_candidates (
 cik TEXT NOT NULL, ticker TEXT NOT NULL, sponsor TEXT NOT NULL, sponsor_norm TEXT NOT NULL,
 eligibility TEXT NOT NULL, source_url TEXT NOT NULL, checked_at TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(cik,ticker)
);
CREATE TABLE IF NOT EXISTS v2c_regulatory_versions (
 version_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, published_at TEXT NOT NULL,
 observed_at TEXT NOT NULL, source_url TEXT NOT NULL, source_hash TEXT NOT NULL,
 previous_window TEXT, new_window TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2c_regulatory_review (
 review_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, source_url TEXT NOT NULL,
 observed_at TEXT NOT NULL, reason TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2c_operation_runs (
 run_id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL,
 started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2c_state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS v2e2_issuer_queue (
 ticker TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, material_fingerprint TEXT,
 material_changed_at TEXT, last_attempt TEXT, last_success TEXT, failure_reason TEXT,
 consecutive_failures INTEGER NOT NULL DEFAULT 0, next_eligible_at TEXT, mapping_generation TEXT,
 state TEXT NOT NULL DEFAULT 'PENDING'
);
CREATE TABLE IF NOT EXISTS v2e2_promotion_decisions (
 decision_id TEXT PRIMARY KEY, ticker TEXT, candidate_id TEXT, evidence_id TEXT,
 accession TEXT, outcome TEXT NOT NULL, reason TEXT NOT NULL, details TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v2e2_document_scans (
 scan_id TEXT PRIMARY KEY, accession TEXT NOT NULL, document_url TEXT NOT NULL,
 source_hash TEXT, extraction_version TEXT NOT NULL, status TEXT NOT NULL,
 completeness TEXT NOT NULL, reason TEXT, scanned_at TEXT NOT NULL,
 UNIQUE(accession, document_url, extraction_version)
);
"""
IMMUTABLE = ('v2c_financial_snapshots', 'v2c_financing_events', 'v2c_regulatory_versions')


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def ensure_schema(conn):
    present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2c_state'").fetchone()
    if present and conn.execute("SELECT 1 FROM v2c_state WHERE key='schema_version' AND payload='2'").fetchone():
        return
    # executescript commits transactions: call at operation boundaries, never mid-transaction.
    conn.executescript(DDL)
    for table in IMMUTABLE:
        for action in ('UPDATE', 'DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} "
                         f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'v2c evidence is immutable'); END")
    state_put(conn, "schema_version", 2)


def state_get(conn, key, default=None):
    row = conn.execute('SELECT payload FROM v2c_state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def state_put(conn, key, value):
    with conn:
        conn.execute('INSERT INTO v2c_state(key,payload) VALUES(?,?) '
                     'ON CONFLICT(key) DO UPDATE SET payload=excluded.payload', (key, encode(value)))


def operation_start(conn, name, *, owner_token=None, parent_token=None, code_version=None):
    ensure_schema(conn)
    with conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(v2c_operation_runs)")]
        if 'owner_token' not in columns:
            for col, typ in (('owner_token','TEXT'),('parent_token','TEXT'),('code_version','TEXT'),('heartbeat_at','TEXT'),('stage','TEXT')):
                conn.execute(f'ALTER TABLE v2c_operation_runs ADD COLUMN {col} {typ}')
        token = owner_token or os.environ.get('MOZES_RUN_TOKEN')
        parent = parent_token or os.environ.get('MOZES_PARENT_TOKEN')
        return conn.execute('INSERT INTO v2c_operation_runs(operation,started_at,status,payload,owner_token,parent_token,code_version,heartbeat_at,stage) '
                            "VALUES(?,?,'RUNNING','{}',?,?,?,?,?)", (name, utcnow(), token, parent, code_version or os.environ.get('GITHUB_SHA'), utcnow(), 'start')).lastrowid


def operation_finish(conn, run_id, status, payload):
    from .source_observability import snapshot
    metrics = snapshot()
    if metrics is not None:
        payload = {**payload, 'source_operations': metrics}
        if status == 'OK' and any(row['errors'] for row in metrics['sources'].values()):
            status = 'PARTIAL'
    with conn:
        conn.execute('UPDATE v2c_operation_runs SET finished_at=?,heartbeat_at=?,status=?,payload=?,stage=? WHERE run_id=?',
                     (utcnow(), utcnow(), status, encode(payload), 'finished', run_id))


def operation_heartbeat(conn, run_id, stage=None, counters=None):
    with conn:
        conn.execute('UPDATE v2c_operation_runs SET heartbeat_at=?,stage=?,payload=? WHERE run_id=? AND status=\'RUNNING\'',
                     (utcnow(), stage or 'working', encode(counters or {}), run_id))


def finalize_owned_runs(conn, owner_token, status, reason):
    if not owner_token:
        return 0
    rows = conn.execute("SELECT run_id,payload FROM v2c_operation_runs WHERE owner_token=? AND status='RUNNING'", (owner_token,)).fetchall()
    with conn:
        for row in rows:
            payload = json.loads(row['payload'] or '{}')
            payload['termination_reason'] = reason
            conn.execute("UPDATE v2c_operation_runs SET finished_at=?,heartbeat_at=?,status=?,stage='finalized',payload=? WHERE run_id=?",
                         (utcnow(), utcnow(), status, encode(payload), row['run_id']))
    refresh_columns = {row[1] for row in conn.execute('PRAGMA table_info(refresh_runs)')}
    if 'owner_token' not in refresh_columns:
        return len(rows)
    refresh_rows = conn.execute("SELECT run_id,details_json FROM refresh_runs WHERE owner_token=? AND status='RUNNING'", (owner_token,)).fetchall()
    with conn:
        for row in refresh_rows:
            payload = json.loads(row['details_json'] or '{}')
            payload['termination_reason'] = reason
            conn.execute("UPDATE refresh_runs SET finished_at=?,heartbeat_at=?,status=?,details_json=? WHERE run_id=?",
                         (utcnow(), utcnow(), status, json.dumps(payload, ensure_ascii=False), row['run_id']))
    return len(rows) + len(refresh_rows)
