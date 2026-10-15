"""Inactive forward-observation capture; no monitoring hook or strategy eligibility.

Reuses existing source archives, model gates and canonical evidence encoding. Explicit
schema initialization is separate from capture and never part of db.connect/bootstrap.
No price storage/selection, historical backfill, paper signal or notification is created.
"""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlsplit

from . import db
from .intelligence_store import digest, encode
from .pit import OUTCOME_KEYS, all_keys

MAX_RECORDS = 10000
MAX_RECORD_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_SOURCES = 64
MAX_ANALYSIS_AGE_SECONDS = 300
utcnow = db.utcnow


class Disabled(ValueError):
    pass


class Conflict(ValueError):
    pass


def _enabled():
    if os.environ.get('MOZES_PROSPECTIVE_CAPTURE') != '1':
        raise Disabled('prospective_capture_disabled')


def _utc(value):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('aware_timestamp_required')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('aware_timestamp_required')
    return stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,159}', value):
        raise ValueError('invalid_capture_identity')
    return value


def _analysis_times(value, captured_at):
    """Validate known knowledge clocks, not future catalyst forecast windows."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'prices', 'bench', 'stock_prices', 'benchmark_prices'} and isinstance(item, (list, tuple)):
                raise ValueError('inline_price_series_unsupported_use_manifest_reference')
            if key in {'published_at', 'retrieved_at', 'captured_at', 'observed_at', 'available_at',
                       'features_as_of', 'updated_at'} and item is not None:
                if isinstance(item, str) and 'T' not in item:
                    if date.fromisoformat(item).isoformat() != item or item > captured_at[:10]:
                        raise ValueError('future_analysis_evidence')
                elif _utc(item) > captured_at:
                    raise ValueError('future_analysis_evidence')
            if key == 'last_close' and isinstance(item, dict) and item.get('date', '') > captured_at[:10]:
                raise ValueError('future_market_observation')
            _analysis_times(item, captured_at)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _analysis_times(item, captured_at)


@contextmanager
def _atomic(conn):
    conn.execute('SAVEPOINT prospective_capture')
    try:
        yield
    except Exception:
        conn.execute('ROLLBACK TO prospective_capture')
        conn.execute('RELEASE prospective_capture')
        raise
    else:
        conn.execute('RELEASE prospective_capture')


def ensure_schema(conn):
    """Explicit additive DDL; offline fixtures now, separate rollout approval later."""
    _enabled()
    with _atomic(conn):
        conn.execute('CREATE TABLE IF NOT EXISTS prospective_evidence ('
                     'capture_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, observation_key TEXT NOT NULL, '
                     'captured_at TEXT NOT NULL, input_hash TEXT NOT NULL, payload_hash TEXT NOT NULL, '
                     'payload TEXT NOT NULL, UNIQUE(event_id,observation_key))')
        conn.execute('CREATE INDEX IF NOT EXISTS prospective_event_time ON prospective_evidence(event_id,captured_at)')
        for action in ('UPDATE', 'DELETE'):
            conn.execute(f'CREATE TRIGGER IF NOT EXISTS prospective_no_{action.lower()} BEFORE {action} ON prospective_evidence '
                         "BEGIN SELECT RAISE(ABORT, 'prospective evidence is immutable'); END")
        conn.execute('CREATE TRIGGER IF NOT EXISTS prospective_no_replace BEFORE INSERT ON prospective_evidence '
                     'WHEN EXISTS(SELECT 1 FROM prospective_evidence WHERE capture_id=NEW.capture_id OR '
                     '(event_id=NEW.event_id AND observation_key=NEW.observation_key)) '
                     "BEGIN SELECT RAISE(ABORT, 'prospective evidence is immutable'); END")


def _forecast(value, captured_at):
    fields = {'origin', 'window', 'created_at', 'discovered_at', 'modified_at', 'available_at'}
    if not isinstance(value, dict) or set(value) - fields:
        raise ValueError('unsupported_forecast_fields')
    out = {k: value.get(k) for k in fields}
    if out['origin'] not in {'issuer_announced', 'inferred_estimate', 'unknown'}:
        raise ValueError('unsupported_forecast_origin')
    window = out['window']
    if window is not None:
        if not isinstance(window, dict) or set(window) != {'start', 'end', 'precision'}:
            raise ValueError('explicit_forecast_window_required')
        for field in ('start', 'end'):
            if not isinstance(window[field], str) or date.fromisoformat(window[field]).isoformat() != window[field]:
                raise ValueError('canonical_forecast_date_required')
        if window['start'] > window['end'] or window['precision'] not in {'day', 'month', 'quarter', 'half', 'year', 'range'}:
            raise ValueError('invalid_forecast_window')
    for field in fields - {'origin', 'window'}:
        if out[field] is not None:
            out[field] = _utc(out[field])
            if out[field] > captured_at:
                raise ValueError('future_forecast_timestamp')
    # These are caller assertions, not proof that a past source version was known.
    return {**out, 'metadata_availability_status': 'caller_asserted_or_unknown'}


def _price_reference(value):
    if value is None or value == {'status': 'unknown'}:
        return {'status': 'unknown'}
    if (not isinstance(value, dict) or set(value) != {'status', 'schema', 'attachment_id', 'manifest_hash'}
            or value['status'] != 'unverified_reference' or value['schema'] != 'mozes-price-staging-manifest-v1'
            or not re.fullmatch('[0-9a-f]{64}', value['manifest_hash'])):
        raise ValueError('unsupported_price_reference_no_inline_prices')
    _identity(value['attachment_id'])
    return value


def _sources(conn, ids, captured_at, exclusions):
    if not isinstance(ids, (list, tuple)) or len(ids) > MAX_SOURCES or len(set(ids)) != len(ids):
        raise ValueError('source_identity_or_count_budget')
    out = []
    if not ids:
        exclusions.append('source_provenance_missing')
    for source_id in sorted(ids):
        _identity(source_id)
        row = db.source_archive_row(conn, source_id)
        if not row:
            exclusions.append('source_archive_missing:' + source_id)
            out.append({'source_id': source_id, 'status': 'missing'})
            continue
        if hashlib.sha256(row['content'].encode('utf-8')).hexdigest() != row['content_hash']:
            raise ValueError('source_archive_integrity_failure')
        url = urlsplit(row['canonical_url'])
        if url.username or url.password or any(k.lower() in {'token', 'api_key', 'apikey', 'access_token', 'key'} for k in parse_qs(url.query)):
            raise ValueError('unsafe_source_reference')
        for field in ('published_at', 'retrieved_at'):
            try:
                at = _utc(row[field])
            except (TypeError, ValueError):
                exclusions.append('source_timestamp_unknown:' + source_id + ':' + field)
            else:
                if at > captured_at:
                    raise ValueError('future_source_evidence')
        out.append({k: row[k] for k in ('source_id', 'canonical_url', 'source_type', 'content_hash',
                                      'published_at', 'retrieved_at')})
    return out


def _checked(row):
    body = json.loads(row['payload'])
    if (digest(body) != row['payload_hash'] or body['capture_id'] != row['capture_id']
            or body['captured_at'] != row['captured_at'] or body['event_id'] != row['event_id']
            or body['input_hash'] != row['input_hash'] or body['eligible'] is not False or body['executable'] is not False):
        raise ValueError('prospective_evidence_integrity_failure')
    return body


def record(conn, *, observation_key, analysis, forecast, source_ids, selection_rule,
           code_sha=None, price_reference=None, announcement_session='unknown'):
    """Capture NOW only, without scoring, trading, fetching, or changing original rows.

    Stable event/observation keys define identity. Changed input under the same key is
    a conflict; new keys append revisions. Capture time cannot be supplied by a caller.
    """
    _enabled()
    _identity(observation_key)
    event_id = _identity(analysis['id'])
    _identity(analysis['ticker'])
    _identity(analysis['type'])
    if all_keys(analysis) & OUTCOME_KEYS:
        raise ValueError('outcome_information_not_allowed')
    keys = all_keys({'analysis': analysis, 'forecast': forecast, 'selection_rule': selection_rule})
    if any(str(k).lower() in {'token', 'api_key', 'secret', 'password', 'authorization', 'sec_user_agent'}
           or str(k).lower().endswith(('_token', '_api_key', '_password')) for k in keys):
        raise ValueError('secret_fields_not_allowed')
    if announcement_session not in {'premarket', 'intraday', 'afterhours', 'unknown'}:
        raise ValueError('unsupported_announcement_session')
    if not isinstance(selection_rule, dict) or set(selection_rule) != {'id', 'definition'}:
        raise ValueError('explicit_selection_rule_required')
    _identity(selection_rule['id'])
    if code_sha is not None and not re.fullmatch('[0-9a-f]{40}', code_sha):
        raise ValueError('invalid_code_sha')
    captured_at = _utc(utcnow())
    at = analysis.get('as_of')
    if not isinstance(at, str):
        raise ValueError('analysis_time_required')
    exclusions = ['execution_session_and_fills_unverified', 'prospective_strategy_not_registered',
                  'forecast_source_binding_unverified', 'price_provenance_unverified_META_M8']
    if 'T' not in at:
        exclusions.append('analysis_time_precision_insufficient')
    if not analysis.get('versions'):
        exclusions.append('model_versions_missing')
    if not code_sha:
        exclusions.append('code_version_unknown')
    forecast = _forecast(forecast, captured_at)
    if forecast['window'] is None or forecast['origin'] == 'unknown':
        exclusions.append('forecast_unknown')
    if not isinstance(source_ids, (list, tuple)) or any(not isinstance(s, str) for s in source_ids):
        raise ValueError('source_identity_required')
    request = dict(analysis=analysis, forecast=forecast, source_ids=sorted(source_ids),
                   selection_rule=selection_rule, code_sha=code_sha,
                   price_reference=_price_reference(price_reference), announcement_session=announcement_session)
    if len(encode(request).encode('utf-8')) > MAX_RECORD_BYTES:
        raise ValueError('capture_byte_budget')
    input_hash = digest(request)
    capture_id = 'PROSPECTIVE-' + digest([event_id, observation_key])
    with _atomic(conn):
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='prospective_evidence' AND type='table'").fetchone():
            raise ValueError('prospective_schema_not_initialized')
        # A retry returns the original capture, even after a new day/gate update.
        # It must not recapture later state under yesterday's observation identity.
        old = conn.execute('SELECT * FROM prospective_evidence WHERE capture_id=?', (capture_id,)).fetchone()
        if old:
            if old['input_hash'] != input_hash:
                raise Conflict('prospective_observation_identity_conflict')
            return _checked(old)
        # No historical imports disguised as new forward decisions. A date-only
        # current-day analysis can be observed, but cannot prove an intraday fill.
        if 'T' in at:
            age = (datetime.fromisoformat(captured_at) - datetime.fromisoformat(_utc(at))).total_seconds()
            if not 0 <= age <= MAX_ANALYSIS_AGE_SECONDS:
                raise ValueError('historical_capture_unsupported')
        elif at != captured_at[:10]:
            raise ValueError('historical_capture_unsupported')
        _analysis_times(analysis, captured_at)
        sources = _sources(conn, source_ids, captured_at, exclusions)
        gates = {name: db.validation_row(conn, name) for name in ('runup', 'hold_through')}
        if any(gate is None for gate in gates.values()):
            exclusions.append('gate_state_unknown')
        inputs = dict(event_id=event_id, ticker=analysis['ticker'], event_type=analysis['type'],
                      analysis=analysis, forecast=forecast, sources=sources, selection_rule=selection_rule,
                      code_sha=code_sha, price_reference=request['price_reference'],
                      announcement_session=announcement_session, model_gates=gates)
        # Bound even the input before loading/writing the ledger; no silent truncation.
        if len(encode(inputs).encode('utf-8')) > MAX_RECORD_BYTES:
            raise ValueError('capture_byte_budget')
        previous = conn.execute('SELECT capture_id,captured_at FROM prospective_evidence WHERE event_id=? '
                                'ORDER BY captured_at DESC,rowid DESC LIMIT 1', (event_id,)).fetchone()
        if previous and previous['captured_at'] > captured_at:
            raise ValueError('capture_clock_moved_backwards')
        payload = {**inputs, 'schema': 1, 'capture_id': capture_id, 'input_hash': input_hash,
                   'observation_key': observation_key, 'captured_at': captured_at,
                   'previous_capture_id': previous['capture_id'] if previous else None,
                   'eligible': False, 'executable': False, 'authoritative': False,
                   'availability': {'observed_by_bio_at': captured_at, 'historical_first_available_at': None},
                   'execution': {'status': 'unobserved', 'actual_fills': None},
                   'exclusions': sorted(set(exclusions))}
        encoded = encode(payload)
        size = len(encoded.encode('utf-8'))
        count, stored_bytes = conn.execute('SELECT count(*),COALESCE(sum(length(CAST(payload AS BLOB))),0) '
                                          'FROM prospective_evidence').fetchone()
        if count >= MAX_RECORDS or stored_bytes + size > MAX_TOTAL_BYTES:
            raise ValueError('prospective_storage_budget')
        if size > MAX_RECORD_BYTES:
            raise ValueError('capture_byte_budget')
        conn.execute('INSERT INTO prospective_evidence VALUES(?,?,?,?,?,?,?)',
                     (capture_id, event_id, observation_key, captured_at, input_hash, digest(payload), encoded))
        return json.loads(encoded)


def as_of(conn, event_id, cutoff):
    """Read retained observations at/before an aware instant; never rescore/reconstruct."""
    cutoff = _utc(cutoff)
    rows = conn.execute('SELECT * FROM prospective_evidence WHERE event_id=? AND captured_at<=? '
                        'ORDER BY captured_at,rowid LIMIT ?', (event_id, cutoff, MAX_RECORDS + 1)).fetchall()
    if len(rows) > MAX_RECORDS:
        raise ValueError('prospective_read_budget')
    return [_checked(row) for row in rows]
