"""Conservative, explainable cash-runway estimates from SEC US-GAAP companyfacts.

This is liquidity context, NOT a probability of financing or an investment score.
Facts filed on as_of are excluded: companyfacts gives filing dates, not disclosure times.
Every input retains tag, period, filing/accession, currency and canonical SEC provenance.
"""
from __future__ import annotations

import json
import math
import time
from datetime import date, timedelta

from . import db
from .intelligence_store import (digest, encode, ensure_schema, operation_finish,
                                 operation_start, utcnow)
from .sec_http import get_text

CASH_TAG = 'CashAndCashEquivalentsAtCarryingValue'
INVESTMENT_TAGS = ('ShortTermInvestments', 'MarketableSecuritiesCurrent', 'AvailableForSaleSecuritiesCurrent')
CFO_TAG = 'NetCashProvidedByUsedInOperatingActivities'
MONTH_DAYS = 365.25 / 12
MAX_STATEMENT_AGE = 180


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _eligible(payload, tag, as_of):
    facts = (((payload.get('facts') or {}).get('us-gaap') or {}).get(tag) or {}).get('units', {}).get('USD', [])
    out = []
    for item in facts:
        try:
            end = date.fromisoformat(item['end'])
            filed = date.fromisoformat(item['filed'])
            form = item.get('form', '')
            if (end >= as_of or filed >= as_of or filed < end or not _finite(item.get('val'))
                    or form not in {'10-Q', '10-K', '10-Q/A', '10-K/A', '8-K', '6-K', '20-F', '20-F/A'}
                    or not item.get('accn')):
                continue
            out.append({**item, 'tag': tag, 'unit': 'USD'})
        except (ValueError, KeyError, TypeError):
            continue
    # Resolve repetitions and restatements only among records public before cutoff.
    unique = {}
    for item in sorted(out, key=lambda f: (f['filed'], f['accn'])):
        unique[(item.get('start'), item['end'])] = item
    return list(unique.values())


def _fact(item):
    return {key: item.get(key) for key in ('tag', 'val', 'unit', 'start', 'end', 'filed', 'accn', 'form')}


def _cash_and_investments(payload, as_of):
    candidates = [x for x in _eligible(payload, CASH_TAG, as_of) if not x.get('start') and x['val'] >= 0]
    if not candidates:
        return None
    cash = max(candidates, key=lambda f: (f['end'], f['filed'], f['accn']))
    # Alternatives overlap. Never add all the liquidity tags together.
    investments = None
    for tag in INVESTMENT_TAGS:
        matches = [x for x in _eligible(payload, tag, as_of)
                   if not x.get('start') and x['end'] == cash['end'] and x['accn'] == cash['accn'] and x['val'] >= 0]
        if matches:
            investments = max(matches, key=lambda f: f['filed'])
            break
    return cash, investments


def _operating_flow(payload, as_of, statement_end):
    candidates = []
    for item in _eligible(payload, CFO_TAG, as_of):
        try:
            duration = (date.fromisoformat(item['end']) - date.fromisoformat(item['start'])).days + 1
        except (KeyError, ValueError, TypeError):
            continue
        if 60 <= duration <= 400 and item['end'] == statement_end:
            candidates.append({**item, 'days': duration})
    if not candidates:
        return None
    quarter = [x for x in candidates if 70 <= x['days'] <= 110]
    if quarter:
        x = max(quarter, key=lambda f: (f['filed'], f['accn']))
        return {'net_flow': x['val'], 'days': x['days'], 'method': 'reported_quarter', 'facts': [_fact(x)]}
    # SEC quarterly cash-flow statements are often year-to-date, not quarterly.
    ytd = max(candidates, key=lambda f: (f['filed'], f['accn'], -f['days']))
    prev = []
    for item in _eligible(payload, CFO_TAG, as_of):
        if item.get('start') != ytd.get('start') or item['end'] >= ytd['end']:
            continue
        span = (date.fromisoformat(ytd['end']) - date.fromisoformat(item['end'])).days
        # Same filing gives a coherent restated basis; cross-filing subtraction is ambiguous.
        if 70 <= span <= 110 and item['accn'] == ytd['accn']:
            prev.append(item)
    if prev:
        p = max(prev, key=lambda f: f['end'])
        days = (date.fromisoformat(ytd['end']) - date.fromisoformat(p['end'])).days
        return {'net_flow': ytd['val'] - p['val'], 'days': days,
                'method': 'quarter_from_same_filing_ytd_difference', 'facts': [_fact(ytd), _fact(p)]}
    return {'net_flow': ytd['val'], 'days': ytd['days'], 'method': 'reported_period_average', 'facts': [_fact(ytd)]}


def calculate_financials(payload, *, as_of, expected_cik=None):
    as_of = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    actual_cik = str(payload.get('cik', '')).lstrip('0')
    if not actual_cik or (expected_cik is not None and actual_cik != str(expected_cik).lstrip('0')):
        raise ValueError('companyfacts CIK does not match the requested issuer')
    source_url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(actual_cik):010d}.json'
    result = {
        'status': 'missing', 'as_of': as_of.isoformat(), 'cik': actual_cik,
        'source_url': source_url, 'source_hash': digest(payload), 'currency': 'USD',
        'cash_usd': None, 'investments_usd': None, 'liquidity_usd': None,
        'burn_per_month_usd': None, 'runway_at_statement_months': None,
        'estimated_remaining_months': None, 'runway_band': 'unknown',
        'provenance': [], 'warnings': [], 'heuristic_only': True,
        'filing_cutoff_exclusive': as_of.isoformat(),
        'note_he': '\u05d0\u05d5\u05de\u05d3\u05df \u05dc\u05e4\u05d9 \u05e7\u05e6\u05d1 \u05d4\u05ea\u05d6\u05e8\u05d9\u05dd \u05d1\u05ea\u05e7\u05d5\u05e4\u05ea \u05d4\u05d3\u05d5\u05d7; \u05dc\u05d0 \u05ea\u05d7\u05d6\u05d9\u05ea \u05d7\u05d1\u05e8\u05d4 \u05d5\u05dc\u05d0 \u05d4\u05e1\u05ea\u05d1\u05e8\u05d5\u05ea \u05d2\u05d9\u05d5\u05e1.',
    }
    if not (payload.get('facts') or {}).get('us-gaap'):
        result['warnings'].append('standard_us_gaap_usd_facts_unavailable')
        return result
    pair = _cash_and_investments(payload, as_of)
    if pair is None:
        result['warnings'].append('cash_fact_missing_before_cutoff')
        return result
    cash, inv = pair
    period_end = cash['end']
    age = (as_of - date.fromisoformat(period_end)).days
    liquidity = cash['val'] + (inv['val'] if inv else 0)
    result.update(status='partial', period_end=period_end, filed=cash['filed'],
                  statement_age_days=age, cash_usd=cash['val'], investments_usd=inv['val'] if inv else None,
                  liquidity_usd=liquidity, liquidity_basis='cash_plus_current_investments' if inv else 'cash_only',
                  provenance=[_fact(cash)] + ([_fact(inv)] if inv else []))
    if inv is None:
        result['warnings'].append('investments_unknown_cash_only_lower_bound')
    flow = _operating_flow(payload, as_of, period_end)
    if flow is None:
        result['warnings'].append('same_period_operating_cash_flow_missing')
        return result
    result['provenance'] += flow['facts']
    result.update(flow_method=flow['method'], flow_days=flow['days'], operating_net_flow_usd=flow['net_flow'])
    if age > MAX_STATEMENT_AGE:
        result.update(status='stale')
        result['warnings'].append('statement_too_old_for_current_runway')
        return result
    if flow['net_flow'] >= 0:
        result.update(status='available', runway_band='not_burning_operating_cash')
        result['warnings'].append('nonnegative_operating_cash_flow_not_infinite_runway')
        return result
    burn = -flow['net_flow'] / flow['days'] * MONTH_DAYS
    at_statement = liquidity / burn
    remaining = max(0, at_statement - age / MONTH_DAYS)
    result.update(status='available', burn_per_month_usd=burn,
                  runway_at_statement_months=at_statement, estimated_remaining_months=remaining,
                  runway_band='short' if remaining < 6 else 'limited' if remaining < 12 else 'longer')
    result['warnings'].append('constant_burn_estimate_excludes_later_financing_capex_and_new_commitments')
    return result


def store_financials(conn, ticker, payload, *, as_of, expected_cik, recorder=None):
    ensure_schema(conn)
    result = calculate_financials(payload, as_of=as_of, expected_cik=expected_cik)
    sid = 'FIN-' + digest([ticker, result['as_of'], result['source_hash']])[:32]
    previous = conn.execute('SELECT payload FROM v2c_financial_snapshots WHERE ticker=? ORDER BY as_of DESC,captured_at DESC LIMIT 1', (ticker.upper(),)).fetchone()
    with conn:
        inserted = conn.execute('INSERT OR IGNORE INTO v2c_financial_snapshots VALUES(?,?,?,?,?,?,?,?)',
                     (sid, ticker.upper(), result['cik'], result['as_of'], utcnow(),
                      result['source_hash'], result['source_url'], encode(result))).rowcount
    if inserted and previous:
        before = json.loads(previous[0])
        if before.get('provenance') != result.get('provenance'):
            if recorder is None:
                from .live_monitor import record_change
                recorder = record_change
            recorder(conn, ticker=ticker.upper(), change_type='cash_runway_updated',
                     previous_value={'cash_usd': before.get('cash_usd'), 'runway_band': before.get('runway_band')},
                     new_value={'cash_usd': result.get('cash_usd'), 'runway_band': result.get('runway_band')},
                     source_url=result['source_url'], source_type='sec_xbrl', verification_state='derived',
                     severity='medium' if result['runway_band'] == 'short' else 'low',
                     identity=['financial-v2c', sid], metadata={'provenance': result['provenance']})
    return result


def financial_context(conn, ticker, *, as_of):
    ensure_schema(conn)
    as_of = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    row = conn.execute('SELECT payload FROM v2c_financial_snapshots WHERE ticker=? AND as_of<=? '
                       'ORDER BY as_of DESC,captured_at DESC LIMIT 1', (ticker.upper(), as_of.isoformat())).fetchone()
    if not row:
        return {'status': 'missing', 'runway_band': 'unknown', 'warnings': ['no_financial_snapshot']}
    result = json.loads(row[0])
    # Re-age estimates for presentation; never mutate the stored evidence snapshot.
    if result.get('period_end'):
        age = (as_of - date.fromisoformat(result['period_end'])).days
        result['statement_age_days'] = age
        if age > MAX_STATEMENT_AGE:
            result.update(status='stale', estimated_remaining_months=None, runway_band='unknown')
        elif result.get('runway_at_statement_months') is not None:
            remaining = max(0, result['runway_at_statement_months'] - age / MONTH_DAYS)
            result.update(estimated_remaining_months=remaining,
                          runway_band='short' if remaining < 6 else 'limited' if remaining < 12 else 'longer')
    result['calculated_as_of'] = as_of.isoformat()
    return result


from .source_observability import observed


@observed
def refresh_financials(conn, *, as_of=None, fetcher=None, budget_seconds=120):
    as_of = as_of or date.today()
    fetcher = fetcher or (lambda cik: json.loads(get_text(
        f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json')))
    rid = operation_start(conn, 'financials')
    result = {'requested': len(db.watch_rows(conn)), 'processed': 0, 'updated': [], 'missing_cik': [], 'errors': [],
              'budget_exhausted': False}
    by_cik = {}
    deadline = time.monotonic() + budget_seconds
    for watch in db.watch_rows(conn):
        if time.monotonic() >= deadline:
            result['budget_exhausted'] = True
            break
        ticker, cik = watch['ticker'], watch.get('cik')
        if not cik:
            result['missing_cik'].append(ticker)
            continue
        try:
            if cik not in by_cik:
                by_cik[cik] = fetcher(cik)
            extracted = store_financials(conn, ticker, by_cik[cik], as_of=as_of, expected_cik=cik)
            result['updated'].append({'ticker': ticker, 'status': extracted['status']})
            result['processed'] += 1
        except Exception as exc:
            result['errors'].append({'ticker': ticker, 'error': str(exc)[:200]})
    result['updated_count'] = len(result['updated'])
    result['error_count'] = len(result['errors'])
    result['completeness_pct'] = round(100 * result['processed'] / result['requested'], 1) if result['requested'] else 100.0
    status = 'PARTIAL' if result['errors'] else 'INCOMPLETE' if result['missing_cik'] or result['budget_exhausted'] else 'OK'
    operation_finish(conn, rid, status, result)
    return {**result, 'status': status}
