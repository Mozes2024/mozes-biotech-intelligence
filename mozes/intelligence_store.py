"""Additive v2C storage. No writes to historical cases, labels or validation gates."""
from __future__ import annotations

import hashlib
import json
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
    if present and conn.execute("SELECT 1 FROM v2c_state WHERE key='schema_version' AND payload='1'").fetchone():
        return
    # executescript commits transactions: call at operation boundaries, never mid-transaction.
    conn.executescript(DDL)
    for table in IMMUTABLE:
        for action in ('UPDATE', 'DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} "
                         f"BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'v2c evidence is immutable'); END")
    state_put(conn, "schema_version", 1)


def state_get(conn, key, default=None):
    row = conn.execute('SELECT payload FROM v2c_state WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def state_put(conn, key, value):
    with conn:
        conn.execute('INSERT INTO v2c_state(key,payload) VALUES(?,?) '
                     'ON CONFLICT(key) DO UPDATE SET payload=excluded.payload', (key, encode(value)))


def operation_start(conn, name):
    ensure_schema(conn)
    with conn:
        return conn.execute('INSERT INTO v2c_operation_runs(operation,started_at,status,payload) '
                            "VALUES(?,?,'RUNNING','{}')", (name, utcnow())).lastrowid


def operation_finish(conn, run_id, status, payload):
    with conn:
        conn.execute('UPDATE v2c_operation_runs SET finished_at=?,status=?,payload=? WHERE run_id=?',
                     (utcnow(), status, encode(payload), run_id))
