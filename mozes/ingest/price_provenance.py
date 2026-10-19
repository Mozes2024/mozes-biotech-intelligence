"""Opt-in, offline response adapters. Legacy/network adapters remain unchanged.

Provider adjustment semantics must be explicitly declared with a policy reference;
an `adjClose` field alone does not prove a split/dividend convention or past availability.
Only allowlisted provenance and numeric observations leave this module. Response bytes,
headers, URLs, provider metadata and credentials are never persisted.
"""
import csv
import hashlib
import io
import json
import math
import re
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 10000
PRICE_BASES = {'raw', 'split_adjusted', 'split_dividend_adjusted', 'unknown'}
VOLUME_BASES = {'raw', 'split_adjusted', 'unknown'}


def utc(value):
    """Require timestamp precision and an explicit offset; never infer a timezone."""
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('timezone_aware_timestamp_required')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('timezone_aware_timestamp_required')
    return stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')


def day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('session_date_required')
    return value


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}', value):
        raise ValueError('invalid_provenance_identity')
    return value


def reference(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError('unsafe_provenance_reference')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('unsafe_provenance_reference')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def number(value, *, volume=False):
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError('invalid_market_price')
    result = float(value)
    if not math.isfinite(result) or (result < 0 if volume else result <= 0):
        raise ValueError('invalid_market_price')
    return result


def prepare_import(provider, body, *, run_id, instrument_id, ticker, captured_at, vintage,
                   start, end, price_basis='unknown', volume_basis='unknown', currency='unknown',
                   adjustment_policy=None, exchange='unknown', session_timezone='unknown',
                   price_field=None, volume_field=None):
    """Parse bounded response/file bytes; no network access or schema side effects.

    Fields and economic bases are independent: provider `close` is not proof of an
    unadjusted price. Explicit field overrides support declared split-adjusted quote
    closes. By default adjusted bases use adj_close/adj_volume. Missing adjusted cells
    never inherit raw cells. CSV may
    supply available_at + availability_reference, an explicit caller assertion that
    still cannot make a later capture eligible at an earlier historical cutoff.
    Yahoo session dates use its declared exchange timezone, not UTC midnight.
    """
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise ValueError('response_byte_budget')
    if provider not in {'csv', 'yahoo', 'tiingo'}:
        raise ValueError('unsupported_provider')
    if not isinstance(instrument_id, str) or ':' not in instrument_id or instrument_id.endswith(':'):
        raise ValueError('namespaced_instrument_identity_required')
    if price_basis not in PRICE_BASES or volume_basis not in VOLUME_BASES:
        raise ValueError('unsupported_adjustment_basis')
    if currency != 'unknown' and not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('invalid_currency')
    if session_timezone != 'unknown':
        ZoneInfo(session_timezone)
    policy = reference(adjustment_policy)
    if (price_basis in {'split_adjusted', 'split_dividend_adjusted'} or volume_basis == 'split_adjusted') and not policy:
        raise ValueError('adjustment_policy_required')
    price_field = price_field or ('raw_close' if price_basis == 'raw' else 'adjusted_close')
    volume_field = volume_field or ('raw_volume' if volume_basis == 'raw' else 'adjusted_volume')
    if price_field not in {'raw_close', 'adjusted_close'} or volume_field not in {'raw_volume', 'adjusted_volume'}:
        raise ValueError('unsupported_price_or_volume_field')
    if (price_field == 'adjusted_close' and price_basis == 'raw') or (volume_field == 'adjusted_volume' and volume_basis == 'raw'):
        raise ValueError('adjusted_field_cannot_be_declared_raw')
    field_basis = dict(raw_close='unknown', adjusted_close='unknown', raw_volume='unknown', adjusted_volume='unknown')
    field_basis[price_field], field_basis[volume_field] = price_basis, volume_basis
    run = dict(run_id=identity(run_id), instrument_id=identity(instrument_id), ticker=identity(ticker),
               provider=provider, adapter_version='price-provenance-v1', captured_at=utc(captured_at),
               vintage=identity(vintage), price_basis=price_basis, volume_basis=volume_basis,
               currency=currency, exchange=identity(exchange), session_timezone=session_timezone,
               start=day(start), end=day(end), frequency='1d', adjustment_policy=policy,
               price_field=price_field, volume_field=volume_field, field_basis=field_basis,
               response_hash=hashlib.sha256(body).hexdigest())
    if run['start'] > run['end']:
        raise ValueError('invalid_date_range')
    rows = []
    if provider == 'csv':
        reader = csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
        if not reader.fieldnames or 'date' not in reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('csv_date_required')
        for r in reader:
            if None in r or (r.get('ticker') and r['ticker'].upper() != ticker.upper()):
                raise ValueError('csv_shape_or_instrument_mismatch')
            rows.append(dict(date=r['date'], raw_close=r.get('close'), adjusted_close=r.get('adj_close'),
                             raw_volume=r.get('volume'), adjusted_volume=r.get('adj_volume'),
                             available_at=r.get('available_at') or None,
                             availability_reference=r.get('availability_reference') or None))
            if len(rows) > MAX_ROWS:
                raise ValueError('observation_row_budget')
    elif provider == 'tiingo':
        data = json.loads(body)
        if not isinstance(data, list) or len(data) > MAX_ROWS:
            raise ValueError('observation_row_budget')
        for r in data:
            if r.get('ticker') and r['ticker'].upper() != ticker.upper():
                raise ValueError('provider_instrument_mismatch')
            rows.append(dict(date=r['date'][:10], raw_close=r.get('close'), adjusted_close=r.get('adjClose'),
                             raw_volume=r.get('volume'), adjusted_volume=r.get('adjVolume')))
    else:
        result = json.loads(body)['chart']['result'][0]
        meta = result.get('meta', {})
        if meta.get('symbol') and meta['symbol'].upper() != ticker.upper():
            raise ValueError('provider_instrument_mismatch')
        if currency != 'unknown' and meta.get('currency') != currency:
            raise ValueError('provider_currency_mismatch')
        if session_timezone == 'unknown' or (meta.get('exchangeTimezoneName') and meta['exchangeTimezoneName'] != session_timezone):
            raise ValueError('provider_session_timezone_required_or_mismatched')
        quote = result['indicators']['quote'][0]
        adj = result['indicators'].get('adjclose', [{}])[0].get('adjclose', [])
        stamps = result.get('timestamp', [])
        if len(stamps) > MAX_ROWS:
            raise ValueError('observation_row_budget')
        def at(values, index):
            return values[index] if index < len(values) else None
        for i, stamp in enumerate(stamps):
            rows.append(dict(date=datetime.fromtimestamp(stamp, ZoneInfo(session_timezone)).date().isoformat(),
                             raw_close=at(quote.get('close', []), i), adjusted_close=at(adj, i),
                             raw_volume=at(quote.get('volume', []), i), adjusted_volume=None))
        if volume_field == 'adjusted_volume' and volume_basis != 'unknown':
            raise ValueError('yahoo_adjusted_volume_not_supplied')
    normalized = []
    seen = set()
    for row in rows:
        session = day(row['date'])
        if session in seen or not run['start'] <= session <= run['end']:
            raise ValueError('duplicate_or_out_of_range_session')
        seen.add(session)
        avail = utc(row['available_at']) if row.get('available_at') else None
        evidence = reference(row.get('availability_reference'))
        if bool(avail) != bool(evidence):
            raise ValueError('availability_timestamp_and_reference_required_together')
        if avail and (avail > run['captured_at'] or avail[:10] < session):
            raise ValueError('invalid_availability_chronology')
        normalized.append(dict(date=session, raw_close=number(row.get('raw_close')),
                               adjusted_close=number(row.get('adjusted_close')),
                               raw_volume=number(row.get('raw_volume'), volume=True),
                               adjusted_volume=number(row.get('adjusted_volume'), volume=True),
                               available_at=avail, availability_reference=evidence))
    normalized.sort(key=lambda r: r['date'])
    if not normalized:
        raise ValueError('empty_price_import')
    return {'run': run, 'rows': normalized}
