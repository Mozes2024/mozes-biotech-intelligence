"""Hebrew-ready financial context for existing cards; never overrides a score."""
from __future__ import annotations

import json
from datetime import date, timedelta

from .financial_intelligence import financial_context, MONTH_DAYS
from .financing_intelligence import financing_context
from .intelligence_store import ensure_schema


def funding_window(financial, window, today):
    remaining = financial.get('estimated_remaining_months')
    if financial.get('status') != 'available' or remaining is None or not window or not window.get('start'):
        return {'status': 'unknown', 'is_forecast': False}
    projected = today + timedelta(days=int(remaining * MONTH_DAYS))
    end = window.get('end') or window['start']
    status = ('estimate_before_window' if projected.isoformat() < window['start'] else
              'estimate_overlaps_window' if projected.isoformat() < end else 'estimate_beyond_window')
    return {'status': status, 'estimated_date': projected.isoformat(), 'is_forecast': False,
            'liquidity_basis': financial.get('liquidity_basis')}


def enrich_financial_context(conn, rows, today):
    ensure_schema(conn)
    by_ticker = {}
    for row in rows:
        ticker = row.get('ticker')
        if not ticker:
            continue
        if ticker not in by_ticker:
            financial = financial_context(conn, ticker, as_of=today)
            events = financing_context(conn, ticker, as_of=today)
            post_statement = [e for e in events if e['published_at'][:10] > (financial.get('period_end') or '9999')]
            financial['later_financing_disclosures'] = len(post_statement)
            financial['requires_financing_reconciliation'] = any(e['kind'] == 'completed_offering' for e in post_statement)
            by_ticker[ticker] = (financial, events)
        financial, events = by_ticker[ticker]
        row['financial_context'] = financial
        row['financing_events'] = events
        row['funding_window'] = funding_window(financial, (row.get('date') or {}).get('window'), today)
        if financial.get('requires_financing_reconciliation'):
            row['funding_window']['status'] = 'later_financing_requires_reconciliation'
    return rows


def operation_health(conn):
    ensure_schema(conn)
    out = {}
    for operation in ('identity', 'financials', 'financing', 'pipeline', 'deep_refresh'):
        row = conn.execute('SELECT * FROM v2c_operation_runs WHERE operation=? ORDER BY run_id DESC LIMIT 1', (operation,)).fetchone()
        if row:
            item = dict(row)
            item['details'] = json.loads(item.pop('payload'))
            out[operation] = item
    return {'modules': out, 'regulatory_review_count': conn.execute('SELECT COUNT(*) FROM v2c_regulatory_review').fetchone()[0],
            'financial_snapshots': conn.execute('SELECT COUNT(*) FROM v2c_financial_snapshots').fetchone()[0],
            'financing_disclosures': conn.execute('SELECT COUNT(*) FROM v2c_financing_events').fetchone()[0]}
