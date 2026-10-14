"""Read-only projection of retained regulatory claims, never a decision workflow."""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlparse

from . import db
from .regulatory_lifecycle import resolve_application

# Budget for this additive public section, including pretty-print overhead.
# Refuse publication, never truncate rows/evidence or mutate the private queue.
MAX_PUBLIC_REVIEW_BYTES = 8 * 1024 * 1024
MAX_PAGES_SITE_BYTES = 1_000_000_000  # Conservative decimal limit, below 1 GiB.
PAGES_EXPORT_RESERVE_BYTES = 1024 * 1024  # Bounded alert feeds, updates and receipt.
PUBLIC_REASONS = {
    'primary_filing_identity_missing', 'source_issuer_does_not_match_ticker',
    'source_not_yet_available', 'outcome_requires_separate_reconciliation',
    'application_not_unambiguously_identified', 'target_window_not_supported',
    'past_window_not_upcoming', 'same_day_conflict_needs_publication_time',
}


def _public_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return value


def review_queue(conn):
    # Do not bootstrap or migrate from a presentation reader.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2c_regulatory_review'").fetchone():
        return {'available': False, 'count': 0, 'items': []}
    events = db.load_events(conn, 'live')
    watches = {row['ticker']: dict(row) for row in conn.execute('SELECT * FROM watch_universe')}
    items = []
    public_bytes = 4096  # Envelope/count and formatting margin.
    for row in conn.execute('SELECT * FROM v2c_regulatory_review ORDER BY observed_at DESC, review_id'):
        raw = row['payload']
        try:
            evidence = json.loads(raw)
        except (TypeError, ValueError):
            evidence = raw
        supported = isinstance(evidence, dict)
        statement = evidence if supported else {}
        source = row['source_url']
        try:
            parsed = urlparse(source)
            cik = re.match(r'/Archives/edgar/data/(\d+)/', parsed.path)
            primary = (parsed.scheme == 'https' and parsed.netloc == 'www.sec.gov' and bool(cik)
                       and not parsed.query and not parsed.fragment)
        except ValueError:
            primary, cik = False, None
        watch = watches.get(row['ticker']) or {}
        issuer_verified = bool(primary and watch.get('cik') and
                               str(watch['cik']).lstrip('0') == cik[1].lstrip('0'))
        # Identity is independently checked against the current verified catalog.
        # Even a bound application does not confirm the pending outcome claim.
        identity_fields_valid = (isinstance(statement.get('statement', ''), str) and
                                 isinstance(statement.get('application_id', ''), str))
        event = resolve_application(events, row['ticker'], statement) if issuer_verified and identity_fields_valid else None
        state = db.event_state(conn, event['id']) if event else None
        application = None
        if event and event.get('application_id') and (state or {}).get('verification_state') == 'VERIFIED':
            application = {'id': event['application_id'], 'event_id': event['id'],
                           'program': event.get('program'), 'status': state['status'],
                           'verification_state': state['verification_state'],
                           'event_timestamp': state.get('event_timestamp')}
        # Raw JSON is private storage, not a public serialization contract. Only
        # literal source evidence from a public SEC reference crosses this boundary.
        public_evidence = {}
        if primary and supported and statement.get('source_id') == source:
            if isinstance(statement.get('statement'), str):
                public_evidence['statement'] = statement['statement']
            if statement.get('catalyst_type') in ('PDUFA', 'ADCOM'):
                public_evidence['catalyst_type'] = statement['catalyst_type']
            if statement.get('form') in ('8-K', '6-K'):
                public_evidence['form'] = statement['form']
        item = {'review_id': row['review_id'], 'ticker': row['ticker'],
                      'issuer_name': watch.get('company') if issuer_verified else None,
                      'issuer_verified': issuer_verified, 'application': application,
                      'source_url': source if primary else None, 'primary_source_reference': bool(primary),
                      'source_reference_withheld': not primary,
                      'reason': row['reason'] if row['reason'] in PUBLIC_REASONS else 'unrecognized_review_reason',
                      'published_at': _public_timestamp(statement.get('filed')) if primary else None,
                      'retrieved_at': _public_timestamp(statement.get('retrieved_at')) if primary else None,
                      'observed_at': _public_timestamp(row['observed_at']),
                      'event_timestamp': _public_timestamp(statement.get('event_timestamp')) if primary else None,
                      'verification_state': 'REVIEW_REQUIRED', 'confirmed_outcome': False,
                      'evidence_available': 'statement' in public_evidence, 'evidence': public_evidence}
        formatted = json.dumps(item, ensure_ascii=False, indent=2, allow_nan=False)
        public_bytes += len(formatted.encode('utf-8')) + 6 * (formatted.count('\n') + 1) + 2
        if public_bytes > MAX_PUBLIC_REVIEW_BYTES:
            raise ValueError('regulatory_review_public_byte_budget exceeded; all rows retained; publication stopped')
        items.append(item)
    # No silent limit: every retained review record remains inspectable.
    return {'available': True, 'count': len(items), 'items': items}
