"""Explicit offline comparison of immutable staging with a legacy snapshot.

No production caller, network, DDL, writer, manifest freeze or eligibility change.
Matching numbers cannot establish the missing provenance of legacy observations.
"""
import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from . import price_staging as staging
from .ingest.price_provenance import identity, number


def _readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    return conn


def _snapshot_hash(path):
    path = Path(path).resolve()
    if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-journal')):
        raise ValueError('quiescent_checkpoint_copy_required')
    result = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def compare(legacy_path, staging_path, stock, benchmark, *, enabled=None):
    """Compare explicit pinned selections; read existing files without initialization.

    Each request is {ticker, selection}; selection follows select_series. Require
    an as_of and run_id for reproducible, point-in-time refusal diagnostics.
    The caller must supply quiescent checkpoint copies, never active DB files.
    """
    if enabled is None:
        enabled = os.environ.get('MOZES_PRICE_SHADOW_ENABLED') == '1'
    if enabled is not True:
        raise staging.Disabled('price_shadow_disabled')
    if Path(legacy_path).resolve() == Path(staging_path).resolve():
        raise ValueError('separate_snapshot_files_required')
    for request in (stock, benchmark):
        identity(request['ticker'])
        selection = request['selection']
        if not selection.get('run_id') or not selection.get('as_of'):
            raise ValueError('pinned_run_and_cutoff_required')
        if 'enabled' in selection:
            raise ValueError('selection_override_refused')
    report = dict(schema=1, mode='offline-shadow', cutover='HOLD',
                  provenance='legacy_basis_vintage_and_availability_unproven',
                  legacy_written=False, staged_written=False)
    snapshot_hashes = [_snapshot_hash(p) for p in (legacy_path, staging_path)]
    report['snapshot_sha256'] = dict(zip(('legacy', 'staging'), snapshot_hashes))
    with closing(_readonly(legacy_path)) as legacy, closing(_readonly(staging_path)) as staged:
        # One consistent read snapshot per independent checkpoint file.
        legacy.execute('BEGIN')
        staged.execute('BEGIN')
        pair = staging.select_pair(staged, stock['selection'], benchmark['selection'], enabled=True)
        if not pair['available']:
            return {**report, 'available': False, 'reason': pair['reason']}
        result = {}
        for name, request in (('stock', stock), ('benchmark', benchmark)):
            selected = pair[name]
            if selected['run']['ticker'] != request['ticker']:
                raise ValueError('selected_ticker_mismatch')
            rows = selected['rows']
            expected = {r['date']: r for r in rows}
            # Bound legacy reads to the reviewed window, including extra sessions.
            old = legacy.execute('SELECT date,close,volume,source FROM prices '
                                 'WHERE ticker=? AND date>=? AND date<=? ORDER BY date LIMIT ?',
                                 (request['ticker'], rows[0]['date'], rows[-1]['date'], staging.MAX_ROWS + 1)).fetchall()
            if len(old) > staging.MAX_ROWS:
                raise ValueError('legacy_row_budget')
            if len({r['date'] for r in old}) != len(old):
                raise ValueError('duplicate_legacy_dates')
            prior = {r['date']: r for r in old}
            for row in old:
                if number(row['close']) is None:
                    raise ValueError('legacy_close_missing')
                number(row['volume'], volume=True)
            differences = []
            for day in sorted(expected.keys() & prior.keys()):
                a, b = prior[day], expected[day]
                for field in ('close', 'volume'):
                    if a[field] != b[field]:
                        differences.append(dict(date=day, field=field, legacy=a[field], staged=b[field]))
            result[name] = dict(ticker=request['ticker'], run_id=selected['run']['run_id'],
                                response_hash=selected['run']['response_hash'],
                                selection_hash=staging.digest(selected),
                                as_of=selected['as_of'], staged_rows=len(rows), legacy_rows=len(old),
                                missing_dates=sorted(expected.keys() - prior.keys()),
                                extra_dates=sorted(prior.keys() - expected.keys()),
                                differences=differences)
        equal = all(not r['differences'] and not r['missing_dates'] and not r['extra_dates'] for r in result.values())
        if snapshot_hashes != [_snapshot_hash(p) for p in (legacy_path, staging_path)]:
            raise ValueError('snapshot_changed_during_comparison')
        return {**report, 'available': True, 'numeric_equal': equal, 'series': result}
