"""Read-only current financial availability; derived runway is not verified runway."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timezone
from urllib.parse import urlparse

from . import db
from .dates import pub_effective
from .financial_intelligence import CASH_TAG, CFO_TAG, MAX_STATEMENT_AGE, MONTH_DAYS, financial_context
from .scoring_common import known_number
from .versions import VERSIONS

PRIMARY_FINANCIAL = {'sec', 'company_ir', 'company_press_release', 'earnings_release'}
RECONCILE_KINDS = {'completed_offering', 'priced_offering', 'multiple_transactions_review', 'financing_review_required'}


def _day(value):
    try:
        if 'T' in value:
            stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return stamp.astimezone(timezone.utc).date() if stamp.tzinfo else None
        return date.fromisoformat(pub_effective(value)) if value else None
    except (ValueError, TypeError):
        return None


def _known(value, cutoff):
    day = _day(value)
    return day is not None and day < cutoff


def _later_financing(conn, ticker, anchor, cutoff):
    if not anchor or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2c_financing_events'").fetchone():
        return []
    disclosures = []
    for row in conn.execute('SELECT * FROM v2c_financing_events WHERE ticker=?', (ticker.upper(),)):
        if (not _known(row['published_at'], cutoff) or not _known(row['captured_at'], cutoff)
                or _day(row['published_at']) < anchor or not row['accession'] or not row['source_hash']):
            continue
        source = urlparse(row['source_url'])
        if source.scheme != 'https' or source.hostname not in {'www.sec.gov', 'data.sec.gov'}:
            continue
        payload = json.loads(row['payload'])
        if payload.get('kind') in RECONCILE_KINDS:
            disclosures.append({'kind': payload['kind'], 'published_at': row['published_at'],
                                'source_url': row['source_url'], 'accession': row['accession']})
    return disclosures


def financial_availability(conn, event, as_of):
    """No schema initialization, source retrieval, cash adjustment, or ledger writes."""
    cutoff = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    result = {'status': 'MISSING', 'information_status': 'MISSING', 'source_inputs_available': False,
              'verified_runway_months': None, 'short_cash_runway': False,
              'missing_requirements': ['source_verified_current_runway'], 'reasons': [],
              'version': VERSIONS['financial_availability']}
    ticker = event.get('ticker')
    context = financial_context(conn, ticker, as_of=cutoff, pit=True) if ticker else {}
    metadata = context.get('snapshot_provenance') or {}
    facts = context.get('provenance') or []
    cash = [fact for fact in facts if fact.get('tag') == CASH_TAG and known_number(fact.get('val')) and fact['val'] >= 0]
    source = urlparse(context.get('source_url') or '')
    valid = bool(cash and metadata.get('source_hash') and _known(metadata.get('as_of'), cutoff) and source.scheme == 'https'
                 and source.hostname == 'data.sec.gov' and metadata.get('source_url') == context.get('source_url')
                 and metadata['source_hash'] == context.get('source_hash')
                 and str(metadata.get('cik', '')).lstrip('0') == str(context.get('cik', '')).lstrip('0')
                 and all(fact.get('unit') == 'USD' and known_number(fact.get('val')) and fact.get('accn')
                         and _known(fact.get('filed'), cutoff) and _known(fact.get('end'), cutoff)
                         and _day(fact['filed']) >= _day(fact['end']) for fact in facts))
    statement = _day(context.get('period_end'))
    anchor = None
    if valid and statement and 0 <= (cutoff - statement).days <= MAX_STATEMENT_AGE:
        result.update(status='SOURCE_BACKED_ESTIMATE', information_status='PARTIAL', source_inputs_available=True,
                      source_url=context['source_url'], source_hash=context['source_hash'],
                      source_known_at=metadata['captured_at'], statement_date=statement.isoformat(),
                      estimated_runway_months=context.get('estimated_remaining_months'), heuristic_only=True)
        if not any(fact.get('tag') == CFO_TAG for fact in facts):
            result['missing_requirements'].append('same_period_operating_cash_flow')
        anchor = statement
    elif valid and statement:
        result.update(status='STALE', reasons=['statement_too_old_for_current_runway'])

    runway = event.get('runway') or {}
    if not isinstance(runway, Mapping):
        runway = {}
    months, known = runway.get('months'), _day(runway.get('known_from'))
    if known_number(months) and months >= 0:
        source_id = runway.get('source_id') or runway.get('src')
        sources = db.load_event_sources(conn, event.get('id'))
        proof = next((item for item in sources if source_id and item['source_id'] == source_id
                      and item['source_type'] in PRIMARY_FINANCIAL and urlparse(item.get('url') or '').scheme == 'https'
                      and item.get('statement') and _known(item.get('published_at'), cutoff)
                      and _known(item.get('retrieved_at'), cutoff)), None)
        if (proof and known and _day(proof['published_at']) <= known < cutoff
                and (cutoff - known).days <= MAX_STATEMENT_AGE and runway.get('heuristic_only') is False):
            remaining = max(0, months - (cutoff - known).days / MONTH_DAYS)
            result.update(status='VERIFIED_RUNWAY', information_status='KNOWN', verified_runway_months=remaining,
                          short_cash_runway=remaining < 12, missing_requirements=[],
                          runway_source={'source_id': proof['source_id'], 'source_url': proof['url'],
                                         'published_at': proof['published_at'], 'source_known_at': proof['retrieved_at']},
                          heuristic_only=False)
            anchor = known
        else:
            result['reasons'].append('curated_runway_not_current_source_verified')
            if result['status'] == 'MISSING':
                result['status'] = 'STALE' if known and (cutoff - known).days > MAX_STATEMENT_AGE else 'HEURISTIC'
    elif known_number(event.get('runway_months')):
        result['reasons'].append('bare_runway_without_source_provenance')
        if result['status'] == 'MISSING':
            result['status'] = 'HEURISTIC'

    later = _later_financing(conn, ticker, anchor, cutoff) if ticker else []
    if later:
        result.update(status='RECONCILIATION_REQUIRED', information_status='PARTIAL',
                      verified_runway_months=None, short_cash_runway=False,
                      missing_requirements=['financial_reconciliation', 'source_verified_current_runway'],
                      later_financing=later)
    return result
