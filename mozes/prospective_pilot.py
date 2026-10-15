"""Bounded optional observer in the existing serialized Python monitor lineage.

No fetching, dispatch, price writer, scoring policy, public signal or gate activation.
Preparation verifies a separately retained authoritative backup before additive DDL.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import db, prospective_capture as capture
from .intelligence_store import digest, encode, state_get, state_put

MAX_EVENTS = 3
MAX_DAILY_RECORDS = 24
MAX_SOURCES = 32
MAX_SOURCE_BYTES = 64 * 1024
MAX_EVENT_BYTES = 128 * 1024
MAX_PRICE_ROWS = 1000
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_SECONDS = 5
HEARTBEAT_SECONDS = 6 * 3600
PREPARATION = 'prospective_pilot_preparation'
HEALTH = 'prospective_pilot_health'
PREFIX = 'PROSPECTIVE-SOURCE-'
RULE = 'prospective-pilot-v1'


def _owner():
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('GITHUB_REPOSITORY') != 'Mozes2024/mozes-biotech-intelligence' or
            os.environ.get('GITHUB_WORKFLOW') != 'lightweight-live-monitor' or
            os.environ.get('GITHUB_REF') != 'refs/heads/main' or
            not re.fullmatch(r'[1-9][0-9]*', os.environ.get('GITHUB_RUN_ID', '')) or
            not re.fullmatch(r'[0-9a-f]{40}', os.environ.get('GITHUB_SHA', '')) or
            os.environ.get('INCIDENT_MODE') == 'recover' or os.environ.get('SOURCE_RUN_ID')):
        raise ValueError('serialized_ordinary_owner_required')
    return os.environ['GITHUB_RUN_ID']


def file_hash(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def verify(conn):
    """Read-only whole-ledger audit, intended for preparation/operator verification."""
    if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('database_integrity_failure')
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='prospective_evidence' AND type='table'").fetchone():
        return {'valid': True, 'capture_schema': False, 'records': 0}
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    if not {'prospective_no_update', 'prospective_no_delete', 'prospective_no_replace'} <= triggers:
        raise ValueError('capture_immutability_triggers_missing')
    count, size = conn.execute('SELECT count(*),COALESCE(sum(length(CAST(payload AS BLOB))),0) FROM prospective_evidence').fetchone()
    if count > capture.MAX_RECORDS or size > capture.MAX_TOTAL_BYTES:
        raise ValueError('capture_storage_budget')
    previous = {}
    for row in conn.execute('SELECT * FROM prospective_evidence ORDER BY captured_at,rowid'):
        body = capture._checked(row)
        if body['previous_capture_id'] != previous.get(body['event_id']):
            raise ValueError('capture_revision_chain_failure')
        previous[body['event_id']] = body['capture_id']
        for source in body['sources']:
            if source.get('status') == 'missing':
                continue
            archived = db.source_archive_row(conn, source['source_id'])
            if (not archived or any(archived[k] != v for k, v in source.items()) or
                    hashlib.sha256(archived['content'].encode()).hexdigest() != source['content_hash']):
                raise ValueError('capture_source_integrity_failure')
    return {'valid': True, 'capture_schema': True, 'records': count, 'payload_bytes': size}


def prepare(path):
    """Existing workflow ONLY, immediately after restore, before source/alert writers.

    The initializer process has capture=1, but invokes no observation. Job capture
    remains off. The operator must have independently downloaded/verified the named
    existing artifact; this function does not assert remote artifact availability.
    """
    owner = _owner()
    proof = json.loads(os.environ.get('MOZES_PROSPECTIVE_PREPARE') or '{}')
    if (set(proof) != {'source_run_id', 'db_sha256'} or
            not re.fullmatch(r'[1-9][0-9]*', str(proof['source_run_id'])) or str(proof['source_run_id']) == owner or
            not re.fullmatch('[0-9a-f]{64}', proof['db_sha256'])):
        raise ValueError('explicit_retained_backup_proof_required')
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=.05)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN IMMEDIATE')
        old = state_get(conn, PREPARATION)
        if old:
            if not verify(conn)['capture_schema']:
                raise ValueError('prepared_schema_missing')
            conn.rollback()
            return {'status': 'ALREADY_PREPARED', 'backup': old['backup']}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='prospective_evidence'").fetchone():
            raise ValueError('unreviewed_existing_capture_schema')
        if conn.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal':
            raise ValueError('standalone_restored_database_required')
        if file_hash(path) != proof['db_sha256']:
            raise ValueError('retained_backup_hash_mismatch')
        if not verify(conn)['valid']:
            raise ValueError('backup_integrity_failure')
        capture.ensure_schema(conn)
        verify(conn)
        marker = {'schema': 1, 'rule': RULE, 'backup': proof, 'prepared_by_run_id': owner,
                  'prepared_at': capture._utc(capture.utcnow()), 'code_sha': os.environ['GITHUB_SHA']}
        conn.execute('INSERT INTO v2c_state(key,payload) VALUES(?,?)', (PREPARATION, encode(marker)))
        conn.commit()
        return {'status': 'PREPARED', 'backup': proof}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _semantic(value):
    if isinstance(value, dict):
        return {k: _semantic(v) for k, v in value.items() if k not in {
            'as_of', 'today', 'updated_at', 'retrieved_at', 'captured_at', 'observed_at',
            'created_at', 'last_checked_at', 'checked_at'}}
    if isinstance(value, list):
        return [_semantic(v) for v in value]
    return value


def _safe_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            any(k.lower() in {'token', 'api_key', 'apikey', 'access_token', 'key', 'authorization'} for k in parse_qs(parsed.query))):
        raise ValueError('unsafe_source_reference')


def _archive(conn, event_id, sources, remaining_bytes, deadline):
    ids, used = [], 0
    if sum(len((s.get('statement') or '').encode()) for s in sources) > MAX_SOURCE_BYTES:
        raise ValueError('source_excerpt_budget')
    for source in sources:
        if time.monotonic() >= deadline:
            raise ValueError('observer_time_budget')
        # Validate references even when the excerpt is absent: the full analysis
        # still includes these source rows in its private frozen envelope.
        _safe_url(source['url'])
        if '://' in str(source['source_id']):
            _safe_url(source['source_id'])
        text = source.get('statement')
        if not text:
            continue
        metadata = {'scope': 'observed_event_source_excerpt_not_http_response', 'event_id': event_id,
                    'upstream_source_id': source['source_id'], 'upstream_source_type': source['source_type'],
                    'upstream_retrieved_at_claimed': source['retrieved_at']}
        source_id = PREFIX + digest([metadata, source['url'], source['published_at'], text])
        old = db.source_archive_row(conn, source_id)
        if old:
            if (old['content'] != text or old['canonical_url'] != source['url'] or
                    old['published_at'] != source['published_at'] or json.loads(old['metadata_json']) != metadata):
                raise ValueError('immutable_source_conflict')
        else:
            size = len(text.encode()) + len(json.dumps(metadata, ensure_ascii=False, sort_keys=True).encode())
            if used + size > remaining_bytes:
                raise ValueError('owned_archive_storage_budget')
            db.archive_source(conn, source_id, source['url'], 'prospective_excerpt', source['published_at'],
                              capture._utc(capture.utcnow()), text, metadata)
            used += size
        ids.append(source_id)
    return ids, used


def _report(conn, report):
    if report.get('source_gaps', 0) > 0:
        report = {**report, 'status': 'INCOMPLETE'}
    report = {**report, 'owner_run_id': os.environ.get('GITHUB_RUN_ID'),
              'at': capture._utc(capture.utcnow()), 'rule': RULE}
    try:
        state_put(conn, HEALTH, report)
        database = conn.execute('PRAGMA database_list').fetchone()[2]
        target = Path(database).parent / 'prospective-pilot.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(encode(report), encoding='utf-8')
        os.replace(temp, target)
    except Exception:
        report['health_persistence_failed'] = True
    return report


def after_monitor(conn, *, monitor_status):
    """Best-effort observer, after ordinary source processing/alert queueing only."""
    try:
        return _observe(conn, monitor_status=monitor_status)
    except Exception as exc:
        # Even a broken connection/health writer must not interrupt monitoring.
        return {'status': 'INCOMPLETE', 'errors': 1, 'error_type': type(exc).__name__,
                'health_persistence_failed': True}


def _observe(conn, *, monitor_status):
    if os.environ.get('MOZES_PROSPECTIVE_CAPTURE') != '1':
        return {'status': 'DISABLED'}
    try:
        owner = _owner()
    except ValueError:
        return {'status': 'SKIPPED', 'reason': 'serialized_ordinary_owner_required'}
    if conn.in_transaction:
        return {'status': 'SKIPPED', 'reason': 'caller_transaction_open'}
    report = {'status': 'OK', 'captured': 0, 'retried': 0, 'unchanged': 0, 'source_gaps': 0, 'errors': 0,
              'skipped': 0, 'elapsed_seconds': 0}
    started = time.monotonic()
    old_timeout = conn.execute('PRAGMA busy_timeout').fetchone()[0]
    conn.execute('PRAGMA busy_timeout=50')
    try:
        if monitor_status != 'OK' or os.environ.get('MOZES_PROSPECTIVE_PREPARE'):
            return _report(conn, {**report, 'status': 'SKIPPED', 'reason': 'monitor_incomplete_or_preparing'})
        if not state_get(conn, PREPARATION):
            return _report(conn, {**report, 'status': 'SKIPPED', 'reason': 'unprepared_schema'})
        events = json.loads(os.environ.get('MOZES_PROSPECTIVE_EVENT_IDS') or '[]')
        if not isinstance(events, list) or not 0 < len(events) <= MAX_EVENTS or len(set(events)) != len(events):
            raise ValueError('invalid_pilot_allowlist')
        for event_id in events:
            capture._identity(event_id)
        now = capture._utc(capture.utcnow())
        daily = conn.execute('SELECT count(*) FROM prospective_evidence WHERE captured_at>=?', (now[:10],)).fetchone()[0]
        count, ledger_bytes = conn.execute('SELECT count(*),COALESCE(sum(length(CAST(payload AS BLOB))),0) '
                                           'FROM prospective_evidence').fetchone()
        if count >= capture.MAX_RECORDS or ledger_bytes >= capture.MAX_TOTAL_BYTES:
            return _report(conn, {**report, 'status': 'INCOMPLETE', 'reason': 'capture_storage_budget', 'skipped': len(events)})
        archived_bytes = conn.execute('SELECT COALESCE(sum(length(CAST(content AS BLOB))+length(CAST(metadata_json AS BLOB))),0) '
                                      "FROM source_archive WHERE source_id>=? AND source_id<?", (PREFIX, 'PROSPECTIVE-SOURCE.')).fetchone()[0]
        from .engine_v2 import score_event
        from .lifecycle import TERMINAL
        for index, event_id in enumerate(sorted(events)):
            if time.monotonic() - started >= MAX_SECONDS or daily >= MAX_DAILY_RECORDS:
                report.update(status='INCOMPLETE', reason='pilot_time_or_daily_budget',
                              skipped=report['skipped'] + len(events) - index)
                break
            key = 'pilot-run:' + owner
            identity = 'PROSPECTIVE-' + digest([event_id, key])
            old = conn.execute('SELECT * FROM prospective_evidence WHERE capture_id=?', (identity,)).fetchone()
            if old:
                capture._checked(old)
                report['retried'] += 1
                continue
            try:
                stored = conn.execute("SELECT payload FROM events WHERE kind='live' AND id=?", (event_id,)).fetchone()
                if not stored or len(stored[0].encode()) > MAX_EVENT_BYTES:
                    raise ValueError('event_missing_or_size_budget')
                event = json.loads(stored[0])
                sources = [dict(r) for r in conn.execute('SELECT * FROM event_sources WHERE event_id=? '
                           'ORDER BY published_at,retrieved_at,source_id LIMIT ?', (event_id, MAX_SOURCES + 1))]
                if len(sources) > MAX_SOURCES:
                    raise ValueError('event_source_count_budget')
                for symbol in (event['ticker'], 'XBI'):
                    if conn.execute('SELECT count(*) FROM prices WHERE ticker=?', (symbol,)).fetchone()[0] > MAX_PRICE_ROWS:
                        raise ValueError('price_context_row_budget')
                analysis = score_event(conn, event, now[:10])
                if ((analysis.get('state') or {}).get('status') in TERMINAL or
                        (analysis.get('classification') or {}).get('class') == 'RESOLVED'):
                    report['skipped'] += 1
                    continue
                fingerprint = digest([_semantic(analysis), sorted(events), RULE])
                previous = conn.execute('SELECT * FROM prospective_evidence WHERE event_id=? ORDER BY captured_at DESC,rowid DESC LIMIT 1',
                                        (event_id,)).fetchone()
                if previous:
                    body = capture._checked(previous)
                    last = (body['analysis'].get('prospective_pilot_context') or {}).get('semantic_fingerprint')
                    age = (datetime.fromisoformat(now) - datetime.fromisoformat(body['captured_at'])).total_seconds()
                    if last == fingerprint and 0 <= age < HEARTBEAT_SECONDS:
                        report['unchanged'] += 1
                        continue
                ids, added = _archive(conn, event_id, sources, MAX_ARCHIVE_BYTES - archived_bytes, started + MAX_SECONDS)
                archived_bytes += added
                if len(ids) != len(sources) or not ids:
                    report['source_gaps'] += 1
                window = (analysis.get('date') or {}).get('window')
                precision = (analysis.get('date') or {}).get('precision')
                forecast = {'origin': 'unknown', 'window': None}
                if window and window.get('start') and window.get('end'):
                    forecast['window'] = {**window, 'precision': 'day' if precision == 'exact' else
                        precision if precision in {'month', 'quarter', 'half', 'year', 'range'} else 'range'}
                analysis['prospective_pilot_context'] = {'semantic_fingerprint': fingerprint,
                    'source_scope': 'excerpt_only' if ids else 'missing', 'producer_run_id': owner}
                capture.record(conn, observation_key=key, analysis=analysis, forecast=forecast, source_ids=ids,
                    selection_rule={'id': RULE, 'definition': {'allowlist': sorted(events), 'heartbeat_seconds': HEARTBEAT_SECONDS,
                        'daily_limit': MAX_DAILY_RECORDS, 'capture_on_semantic_change': True}},
                    code_sha=os.environ['GITHUB_SHA'], announcement_session=(analysis.get('state') or {}).get('event_session') or 'unknown')
                report['captured'] += 1
                daily += 1
            except Exception as exc:
                report['status'] = 'INCOMPLETE'
                report['errors'] += 1
                report['error_type'] = type(exc).__name__
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        return _report(conn, report)
    except Exception as exc:
        return _report(conn, {**report, 'status': 'INCOMPLETE', 'errors': report['errors'] + 1,
                              'error_type': type(exc).__name__})
    finally:
        try:
            conn.execute(f'PRAGMA busy_timeout={int(old_timeout)}')
        except sqlite3.Error:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify'))
    parser.add_argument('--database', default=os.environ.get('MOZES_DB_PATH', '.monitor/mozes-live.db'))
    args = parser.parse_args()
    if args.action == 'prepare':
        print(encode(prepare(args.database)))
    else:
        conn = sqlite3.connect(Path(args.database).resolve().as_uri() + '?mode=ro', uri=True)
        conn.row_factory = sqlite3.Row
        try:
            print(encode(verify(conn)))
        finally:
            conn.close()


if __name__ == '__main__':
    main()
