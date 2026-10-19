"""Disabled-by-default price staging, never connected to legacy prices or monitors.

Explicit initialization uses a SEPARATE database. No automatic production migration,
legacy projection, backfill, scoring, delivery, or public export is performed here.
SQLite triggers enforce append-only rows including INSERT OR REPLACE protection.
"""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from .ingest.price_provenance import (MAX_ROWS, PRICE_BASES, VOLUME_BASES, canonical,
                                     day, identity, number, reference, utc)

MAX_IMPORTS = 64
MAX_RUNS = 10000
MAX_OBSERVATIONS = 1000000
MAX_ATTACHMENTS = 10000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024


class Disabled(ValueError):
    pass


class Conflict(ValueError):
    pass


class Unavailable(ValueError):
    pass


def gate(kind, enabled):
    if enabled is None:
        enabled = os.environ.get(f'MOZES_PRICE_{kind}_ENABLED') == '1'
    if enabled is not True:
        raise Disabled(f'price_{kind.lower()}_disabled')


@contextmanager
def atomic(conn):
    # Savepoints preserve caller transaction ownership and roll back an entire batch.
    conn.execute('SAVEPOINT price_staging')
    try:
        yield
    except Exception:
        conn.execute('ROLLBACK TO price_staging')
        conn.execute('RELEASE price_staging')
        raise
    else:
        conn.execute('RELEASE price_staging')


def connect(path, *, enabled=None):
    gate('STAGING', enabled)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # Refuse legacy/foreign databases, including production DBs, before DDL.
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        allowed = {'staged_price_runs', 'staged_price_observations', 'staged_price_attachments'}
        # ANALYZE adds planner statistics; never exempt the whole sqlite_* namespace.
        statistics = {'sqlite_stat1', 'sqlite_stat4'}
        if tables - allowed - statistics:
            raise ValueError('separate_staging_database_required')
        conn.execute('PRAGMA foreign_keys=ON')
        with atomic(conn):
            conn.execute('CREATE TABLE IF NOT EXISTS staged_price_runs (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS staged_price_observations ('
                         'observation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES staged_price_runs(run_id), '
                         'trading_date TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(run_id,trading_date))')
            conn.execute('CREATE TABLE IF NOT EXISTS staged_price_attachments ('
                         'attachment_id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, payload TEXT NOT NULL)')
            for table in sorted(allowed):
                key = 'run_id' if table.endswith('runs') else 'observation_id' if table.endswith('observations') else 'attachment_id'
                collision = f'{key}=NEW.{key}'
                if table.endswith('observations'):
                    collision += ' OR (run_id=NEW.run_id AND trading_date=NEW.trading_date)'
                for action in ('UPDATE', 'DELETE'):
                    conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action} BEFORE {action} ON {table} "
                                 "BEGIN SELECT RAISE(ABORT, 'immutable_price_staging'); END")
                conn.execute(f'CREATE TRIGGER IF NOT EXISTS {table}_replace BEFORE INSERT ON {table} '
                             f'WHEN EXISTS(SELECT 1 FROM {table} WHERE {collision}) '
                             "BEGIN SELECT RAISE(ABORT, 'immutable_price_staging'); END")
        return conn
    except Exception:
        conn.close()
        raise


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_batch(batch):
    """Revalidate the public write boundary, not just the trusted adapter caller."""
    run, rows = batch['run'], batch['rows']
    required = {'run_id', 'instrument_id', 'ticker', 'provider', 'adapter_version', 'captured_at',
                'vintage', 'price_basis', 'volume_basis', 'currency', 'exchange', 'session_timezone',
                'start', 'end', 'frequency', 'adjustment_policy', 'response_hash',
                'price_field', 'volume_field', 'field_basis'}
    if set(run) != required or run['provider'] not in {'csv', 'yahoo', 'tiingo'}:
        raise ValueError('invalid_run_metadata')
    for field in ('run_id', 'instrument_id', 'ticker', 'vintage', 'exchange', 'adapter_version'):
        identity(run[field])
    if ':' not in run['instrument_id'] or run['instrument_id'].endswith(':'):
        raise ValueError('namespaced_instrument_identity_required')
    if run['price_basis'] not in PRICE_BASES or run['volume_basis'] not in VOLUME_BASES:
        raise ValueError('unsupported_adjustment_basis')
    if run['adapter_version'] != 'price-provenance-v1':
        raise ValueError('unsupported_adapter_version')
    if run['price_field'] not in {'raw_close', 'adjusted_close'} or run['volume_field'] not in {'raw_volume', 'adjusted_volume'}:
        raise ValueError('unsupported_price_or_volume_field')
    expected_basis = dict(raw_close='unknown', adjusted_close='unknown', raw_volume='unknown', adjusted_volume='unknown')
    expected_basis[run['price_field']] = run['price_basis']
    expected_basis[run['volume_field']] = run['volume_basis']
    if run['field_basis'] != expected_basis:
        raise ValueError('inconsistent_field_basis')
    if ((run['price_field'] == 'adjusted_close' and run['price_basis'] == 'raw') or
            (run['volume_field'] == 'adjusted_volume' and run['volume_basis'] == 'raw')):
        raise ValueError('adjusted_field_cannot_be_declared_raw')
    if run['currency'] != 'unknown' and not re.fullmatch(r'[A-Z]{3}', run['currency']):
        raise ValueError('invalid_currency')
    if run['session_timezone'] != 'unknown':
        ZoneInfo(run['session_timezone'])
    reference(run['adjustment_policy'])
    if (run['price_basis'] in {'split_adjusted', 'split_dividend_adjusted'} or run['volume_basis'] == 'split_adjusted') and not run['adjustment_policy']:
        raise ValueError('adjustment_policy_required')
    if utc(run['captured_at']) != run['captured_at'] or run['frequency'] != '1d':
        raise ValueError('invalid_capture_or_frequency')
    if len(run['response_hash']) != 64 or any(c not in '0123456789abcdef' for c in run['response_hash']):
        raise ValueError('invalid_response_hash')
    if day(run['start']) > day(run['end']) or not rows or len(rows) > MAX_ROWS:
        raise ValueError('observation_row_budget_or_range')
    seen = set()
    for row in rows:
        if set(row) != {'date', 'raw_close', 'adjusted_close', 'raw_volume', 'adjusted_volume', 'available_at', 'availability_reference'}:
            raise ValueError('invalid_observation_fields')
        session = day(row['date'])
        if session in seen or not run['start'] <= session <= run['end']:
            raise Conflict('duplicate_or_out_of_range_session')
        seen.add(session)
        for field in ('raw_close', 'adjusted_close', 'raw_volume', 'adjusted_volume'):
            if number(row[field], volume='volume' in field) != row[field]:
                raise ValueError('normalized_numeric_observation_required')
        avail, ref = row['available_at'], reference(row['availability_reference'])
        if bool(avail) != bool(ref) or (avail and (utc(avail) != avail or avail > run['captured_at'] or avail[:10] < session)):
            raise ValueError('invalid_availability_provenance')
    return run, sorted(rows, key=lambda r: r['date'])


def stage_imports(conn, batches, *, enabled=None):
    gate('STAGING', enabled)
    if not isinstance(batches, (list, tuple)) or not 0 < len(batches) <= MAX_IMPORTS:
        raise ValueError('import_batch_budget')
    prepared = [validate_batch(b) for b in batches]
    with atomic(conn):
        for run, rows in prepared:
            payload = canonical(run)
            existing = conn.execute('SELECT payload FROM staged_price_runs WHERE run_id=?', (run['run_id'],)).fetchone()
            stored = [json.loads(r[0]) for r in conn.execute(
                'SELECT payload FROM staged_price_observations WHERE run_id=? ORDER BY trading_date', (run['run_id'],))]
            if existing:
                if existing[0] != payload or canonical(stored) != canonical(rows):
                    raise Conflict('immutable_run_conflict')
                continue
            if (conn.execute('SELECT count(*) FROM staged_price_runs').fetchone()[0] >= MAX_RUNS or
                    conn.execute('SELECT count(*) FROM staged_price_observations').fetchone()[0] + len(rows) > MAX_OBSERVATIONS):
                raise ValueError('staging_storage_budget')
            conn.execute('INSERT INTO staged_price_runs VALUES (?,?)', (run['run_id'], payload))
            conn.executemany('INSERT INTO staged_price_observations VALUES (?,?,?,?)',
                             [(digest({'run_id': run['run_id'], 'date': r['date']}), run['run_id'], r['date'], canonical(r)) for r in rows])


def refused(reason):
    return {'available': False, 'reason': reason}


def select_series(conn, *, instrument_id, dates, provider, price_basis, volume_basis, currency,
                  vintage, run_id=None, as_of=None, enabled=None):
    gate('SELECTION', enabled)
    if not isinstance(dates, (list, tuple)) or not dates or len(dates) > MAX_ROWS:
        return refused('invalid_trading_dates')
    try:
        requested = sorted(day(d) for d in dates)
        cutoff = utc(as_of) if as_of is not None else None
    except (ValueError, TypeError):
        return refused('invalid_date_or_cutoff')
    if len(set(requested)) != len(requested):
        return refused('duplicate_trading_dates')
    if any(v in (None, '', 'unknown') for v in (instrument_id, provider, price_basis, volume_basis, currency, vintage)):
        return refused('unknown_provenance')
    filters = dict(instrument_id=instrument_id, provider=provider, price_basis=price_basis,
                   volume_basis=volume_basis, currency=currency, vintage=vintage)
    candidates = []
    # Metadata is bounded by MAX_RUNS. Selection never reads the legacy prices table.
    for row in conn.execute('SELECT payload FROM staged_price_runs'):
        run = json.loads(row[0])
        if all(run[k] == v for k, v in filters.items()) and (run_id is None or run['run_id'] == run_id):
            candidates.append(run)
    if not candidates:
        return refused('no_compatible_run')
    # Latest matching capture, stable run-ID tie break; no silent fallback to an older
    # complete run if the chosen run is partial. Callers can explicitly pin an older ID.
    run = max(candidates, key=lambda r: (r['captured_at'], r['run_id']))
    if cutoff and run['captured_at'] > cutoff:
        return refused('capture_after_cutoff')
    if run['exchange'] == 'unknown' or run['session_timezone'] == 'unknown':
        return refused('unknown_session_convention')
    stored = {r['trading_date']: r for r in conn.execute(
        'SELECT observation_id,trading_date,payload FROM staged_price_observations WHERE run_id=?', (run['run_id'],))}
    if set(requested) - stored.keys():
        return refused('missing_trading_dates')
    selected = []
    for session in requested:
        record = stored[session]
        row = json.loads(record['payload'])
        close = row[run['price_field']]
        volume = row[run['volume_field']]
        if close is None:
            return refused('missing_selected_close')
        if cutoff:
            if not row['available_at'] or not row['availability_reference']:
                return refused('unproven_availability')
            if row['available_at'] > cutoff:
                return refused('source_after_cutoff')
        selected.append({**row, 'observation_id': record['observation_id'], 'close': close, 'volume': volume})
    return dict(available=True, run=run, rows=selected, as_of=cutoff,
                selection_policy='latest-matching-capture-then-run-id-v1; no-splicing; explicit-session-dates')


def select_pair(conn, stock, benchmark, *, enabled=None):
    gate('SELECTION', enabled)
    # A single read transaction prevents different snapshots between the two series.
    with atomic(conn):
        a = select_series(conn, **stock, enabled=True)
        if not a['available']:
            return refused('stock:' + a['reason'])
        b = select_series(conn, **benchmark, enabled=True)
        if not b['available']:
            return refused('benchmark:' + b['reason'])
        if [r['date'] for r in a['rows']] != [r['date'] for r in b['rows']]:
            return refused('unmatched_trading_dates')
        if a['as_of'] != b['as_of']:
            return refused('unmatched_cutoffs')
        if a['run']['price_basis'] != b['run']['price_basis'] or a['run']['currency'] != b['run']['currency']:
            return refused('incompatible_return_conventions')
        return dict(available=True, stock=a, benchmark=b)


def freeze_attachment(conn, attachment_id, case_id, stock, benchmark, *, enabled=None):
    """New namespace only. Never regenerates or writes legacy historical attachments."""
    gate('SELECTION', enabled)
    gate('STAGING', enabled)
    identity(attachment_id)
    identity(case_id)
    with atomic(conn):
        pair = select_pair(conn, stock, benchmark, enabled=True)
        if not pair['available']:
            raise Unavailable(pair['reason'])
        manifest = dict(schema_version=1, attachment_id=attachment_id, case_id=case_id,
                        stock=pair['stock'], benchmark=pair['benchmark'])
        payload = canonical(manifest)
        if len(payload.encode()) > MAX_MANIFEST_BYTES:
            raise ValueError('attachment_manifest_budget')
        existing = conn.execute('SELECT payload FROM staged_price_attachments WHERE attachment_id=?', (attachment_id,)).fetchone()
        if existing:
            if existing[0] != payload:
                raise Conflict('immutable_attachment_conflict')
            return json.loads(existing[0])
        if conn.execute('SELECT count(*) FROM staged_price_attachments').fetchone()[0] >= MAX_ATTACHMENTS:
            raise ValueError('attachment_count_budget')
        conn.execute('INSERT INTO staged_price_attachments VALUES (?,?,?)', (attachment_id, digest(manifest), payload))
        return manifest
