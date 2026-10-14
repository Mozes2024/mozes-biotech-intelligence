"""Read-only projection of retained regulatory claims, never a decision workflow."""
from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from . import db
from .regulatory_lifecycle import resolve_application


def review_queue(conn):
    # Do not bootstrap or migrate from a presentation reader.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2c_regulatory_review'").fetchone():
        return {'available': False, 'count': 0, 'items': []}
    events = db.load_events(conn, 'live')
    watches = {row['ticker']: dict(row) for row in conn.execute('SELECT * FROM watch_universe')}
    items = []
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
            primary = parsed.scheme == 'https' and parsed.netloc == 'www.sec.gov' and bool(cik)
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
        items.append({'review_id': row['review_id'], 'ticker': row['ticker'],
                      'issuer_name': watch.get('company') if issuer_verified else None,
                      'issuer_verified': issuer_verified, 'application': application,
                      'source_url': source, 'primary_source_reference': bool(primary),
                      'reason': row['reason'], 'published_at': statement.get('filed'),
                      'retrieved_at': statement.get('retrieved_at'), 'observed_at': row['observed_at'],
                      'event_timestamp': statement.get('event_timestamp'),
                      'verification_state': 'REVIEW_REQUIRED', 'confirmed_outcome': False,
                      'evidence_available': supported, 'evidence': evidence})
    # No silent limit: every retained review record remains inspectable.
    return {'available': True, 'count': len(items), 'items': items}
