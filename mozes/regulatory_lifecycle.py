"""Bind guidance to an application, never to a date-derived event ID.

Unknown applications go to a review queue. Known applications update one live row and
append source-provenanced guidance versions. Filing acceptance time is NOT event time.
"""
from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urlparse

from . import db
from .data_loader import load_sources
from .dates import date_info, normalize_date_text, pub_effective, PRECISION_CONF
from .intelligence_store import digest, encode, ensure_schema, utcnow
from .lifecycle import TERMINAL

# Only unambiguous identities already in the curated catalog. Adding a new issuer/drug
# is a separate reviewed action; ticker + PDUFA + date is insufficient identification.
ALIASES = {
    'SMMT-PDUFA': (('ivonescimab',), ('HARMONi',)),
    'CAPR-PDUFA': (('deramiocel',), ('HOPE-3',)),
    'CYTK-SNDA': (('aficamten', 'MYQORZO'), ('MAPLE-HCM',)),
    'PHAR-SNDA-LOWER-WEIGHT': (('Joenja', 'leniolisib'), ('lower-dose', 'lower doses', '13-27 kg', '13 to 27 kg')),
    'REGN-POZELIMAB': (('pozelimab',), ('VEXAS',)),
}


def _contains(text, term):
    return bool(re.search(r'(?<![\w-])' + re.escape(term) + r'(?![\w-])', text, re.I))


def resolve_application(events, ticker, statement):
    explicit = statement.get('application_id')
    text = statement.get('statement', '')
    candidates = []
    for event in events:
        if event.get('ticker') != ticker:
            continue
        if statement.get('catalyst_type') == 'ADCOM' and event.get('type') != 'ADCOM':
            continue
        if statement.get('catalyst_type') == 'PDUFA' and not str(event.get('type', '')).startswith('PDUFA'):
            continue
        if explicit and explicit in {event.get('application_id'), event.get('id')}:
            candidates.append(event)
            continue
        if explicit:
            continue
        aliases = ALIASES.get(event['id'])
        if aliases and all(any(_contains(text, term) for term in group) for group in aliases):
            candidates.append(event)
    return candidates[0] if len(candidates) == 1 else None


def target_window(statement, filed):
    text = statement.get('statement') or ''
    # Prefer the NEW target in "extended from A to B"; the old extractor takes A.
    moved = re.search(r'\b(?:extended|revised|changed|moved|delayed)\b.{0,300}?\bto\s+(.+)', text, re.I)
    anchor = re.search(r'(?:PDUFA.{0,25}?date|target action date)\s*(?:of|is|on|was set for|has been set for|:)?\s*(.+)', text, re.I)
    part = moved[1] if moved else anchor[1] if anchor else text
    part = re.split(r'\b(?:previously|formerly|prior target)\b', part, flags=re.I)[0]
    window = normalize_date_text(part, date.fromisoformat(filed[:10]), confirmed_by_company=True)
    if window.precision in {'unknown', 'relative'} or window.inferred_year:
        return None
    return {'start': window.start, 'end': window.end, 'precision': window.precision, 'original': window.original}


def _review(conn, ticker, statement, reason):
    source = statement.get('source_id') or ''
    rid = 'REG-REVIEW-' + digest([ticker, source, statement.get('statement'), reason])[:28]
    with conn:
        conn.execute('INSERT OR IGNORE INTO v2c_regulatory_review VALUES(?,?,?,?,?,?)',
                     (rid, ticker, source, utcnow(), reason, encode(statement)))
    return {'status': 'review', 'reason': reason, 'review_id': rid}


def apply_guidance(conn, ticker, statement, *, today=None):
    ensure_schema(conn)
    today = today or date.today()
    if statement.get('catalyst_type') not in {'PDUFA', 'ADCOM'}:
        return {'status': 'ignored', 'reason': 'not_regulatory_guidance'}
    form = statement.get('form') or ''
    source = statement.get('source_id') or ''
    parsed = urlparse(source)
    filed = statement.get('filed')
    if (form not in {'8-K', '6-K'} or parsed.scheme != 'https' or parsed.hostname != 'www.sec.gov'
            or not parsed.path.startswith('/Archives/edgar/data/') or not filed):
        return _review(conn, ticker, statement, 'primary_filing_identity_missing')
    watch = conn.execute('SELECT cik FROM watch_universe WHERE ticker=?', (ticker,)).fetchone()
    source_cik = re.match(r'/Archives/edgar/data/(\d+)/', parsed.path)
    if watch and watch['cik'] and (not source_cik or str(watch['cik']).lstrip('0') != source_cik[1].lstrip('0')):
        return _review(conn, ticker, statement, 'source_issuer_does_not_match_ticker')
    if filed[:10] > today.isoformat():
        return _review(conn, ticker, statement, 'source_not_yet_available')
    # This layer does not label outcomes or infer an approval from guidance wording.
    text = statement.get('statement', '')
    if re.search(r'\b(?:FDA approved|received approval|complete response letter was issued)\b', text, re.I):
        return _review(conn, ticker, statement, 'outcome_requires_separate_reconciliation')
    event = resolve_application(db.load_events(conn, 'live'), ticker, statement)
    if not event:
        return _review(conn, ticker, statement, 'application_not_unambiguously_identified')
    state = db.event_state(conn, event['id']) or {}
    if state.get('status') in TERMINAL:
        return {'status': 'ignored', 'reason': 'terminal_application_not_reopened', 'event_id': event['id']}
    window = target_window(statement, filed)
    if not window or not window.get('end'):
        return _review(conn, ticker, statement, 'target_window_not_supported')
    if window['end'] < today.isoformat():
        return _review(conn, ticker, statement, 'past_window_not_upcoming')
    current_sources = dict(load_sources())
    for s in db.load_event_sources(conn, event['id']):
        current_sources[s['source_id']] = {'reliability': 'primary' if s['source_type'] == 'sec' else 'secondary'}
    info = date_info(event.get('chronology', []), current_sources)
    old = info.get('window')
    active_dates = [pub_effective(c.get('date')) for c in event.get('chronology', [])
                    if c.get('date_text') and not c.get('superseded')]
    latest_pub = max(active_dates, default='0001-01-01')
    latest_version = conn.execute('SELECT published_at FROM v2c_regulatory_versions WHERE event_id=? '
                                  'ORDER BY published_at DESC LIMIT 1', (event['id'],)).fetchone()
    latest_pub = max(latest_pub, latest_version[0][:10] if latest_version else '0001-01-01')
    if filed[:10] < latest_pub:
        return {'status': 'ignored', 'reason': 'older_source', 'event_id': event['id']}
    new = {k: window[k] for k in ('start', 'end')}
    if old and old != new and old['start'] >= new['start'] and old['end'] <= new['end']:
        return {'status': 'ignored', 'reason': 'broader_restatement_does_not_erase_precision', 'event_id': event['id']}
    source_hash = digest(text)
    version_id = 'REGV-' + digest([event['id'], source, new, source_hash])[:32]
    if conn.execute('SELECT 1 FROM v2c_regulatory_versions WHERE version_id=?', (version_id,)).fetchone():
        return {'status': 'unchanged', 'event_id': event['id']}
    if old and old != new and filed[:10] == latest_pub:
        # Date-only source timestamps cannot safely order conflicting same-day disclosures.
        return _review(conn, ticker, statement, 'same_day_conflict_needs_publication_time')
    action = 'reaffirmed' if old == new else 'narrowed' if old and old['start'] <= new['start'] and old['end'] >= new['end'] else 'revised'
    chronology = [dict(x) for x in event.get('chronology', [])]
    if action != 'reaffirmed':
        for c in chronology:
            if c.get('date_text'):
                c['superseded'] = True
    chronology.append({'date': filed[:10], 'src': source, 'date_text': window['original'], 'text': text})
    event.update(chronology=chronology, application_id=event.get('application_id') or event['id'])
    with conn:
        conn.execute('INSERT INTO v2c_regulatory_versions VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (version_id, event['id'], filed, utcnow(), source, source_hash,
                      encode(old), encode(new), action, encode(statement)))
        conn.execute('UPDATE events SET payload=? WHERE id=?', (encode(event), event['id']))
        conn.execute('INSERT OR IGNORE INTO sources(id,url,source_type,reliability,published,retrieved,title) '
                     "VALUES(?,?,'sec','primary',?,?,?)", (source, source, filed, utcnow(), text[:160]))
        conn.execute("INSERT OR IGNORE INTO event_sources(event_id,source_id,source_type,url,published_at,statement,supports_date,retrieved_at) "
                     "VALUES(?,?,'sec',?,?,?,1,?)", (event['id'], source, source, filed, text, utcnow()))
        conn.execute("INSERT INTO event_state(event_id,status,verification_state,verification_confidence,event_session,updated_at,note) "
                     "VALUES(?,?,'VERIFIED',?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET status=excluded.status,"
                     "verification_state=excluded.verification_state,verification_confidence=excluded.verification_confidence,"
                     "updated_at=excluded.updated_at,note=excluded.note",
                     (event['id'], 'SCHEDULED' if window['precision'] == 'exact' else 'VERIFIED',
                      PRECISION_CONF[window['precision']], state.get('event_session', 'unknown'), utcnow(),
                      'Source-provenanced application guidance: ' + action))
    # event_timestamp is deliberately NOT assigned filing.accepted.
    return {'status': 'updated', 'action': action, 'event_id': event['id'], 'old_window': old, 'window': new}


def quarantine_unbound_auto_events(conn):
    """Retain old auto-discovered rows as evidence but block unidentified applications."""
    count = 0
    for event in db.load_events(conn, 'live'):
        if not event['id'].startswith('AUTO-REG-') or event.get('application_id'):
            continue
        s = db.event_state(conn, event['id']) or {}
        if s.get('status') in TERMINAL or s.get('status') == 'QUARANTINED':
            continue
        db.upsert_event_state(conn, event['id'], status='QUARANTINED', verification_state='QUARANTINED',
                              verification_confidence=0, note='v2C: application identity requires review; date-only ID is unsafe')
        count += 1
    return count
