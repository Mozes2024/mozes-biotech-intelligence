"""Recover the strongest primary-source timing when later filing prose is weaker."""
from __future__ import annotations

import json
import re
from datetime import datetime

from . import db
from .lifecycle import is_resolved
from .promotion import candidate_matches_statement

FORWARD = re.compile(r"\b(topline|top-line|readout|expected|anticipated|scheduled|"
                     r"target action|pdufa|to be presented|will present)\b", re.I)
GENERIC = {'FINAL', 'PHASE', 'EVENT', 'STUDY', 'DATA', 'TRIAL', 'TOPLINE',
           'ANALYSIS', 'RESULTS', 'THERAPY', 'CLINICAL', 'PATIENTS'}


def _same_program(candidate, statement, accepted_quote):
    if candidate_matches_statement(candidate, statement)[0]:
        return True
    # The accepted primary quote can establish a trial acronym absent from CT.gov.
    # Require an exact distinctive token in both primary statements.
    aliases = {token for token in re.findall(r'\b[A-Z][A-Z0-9-]{3,}\b', accepted_quote)
               if token not in GENERIC and not token.startswith('NCT')}
    return any(re.search(r'(?<![A-Za-z0-9])' + re.escape(token) + r'(?![A-Za-z0-9])',
                         statement) for token in aliases)


def _progress_as_of(statement):
    match = re.search(r'\bas of ([A-Z][a-z]+ \d{1,2}, \d{4})\b',
                      statement.get('statement') or '', re.I)
    if match:
        try:
            return datetime.strptime(match.group(1).title(), '%B %d, %Y').date().isoformat()
        except ValueError:
            pass
    return statement.get('published_at')


def _best_facet(event):
    facets = event.get('timing_facets') or []
    counted = [f for f in facets if f.get('timing_mode') == 'EVENT_DRIVEN'
               and (f.get('trigger') or {}).get('trigger_target') is not None]
    if counted:
        return max(counted, key=lambda f: (f.get('published_at') or '',
                   (f.get('trigger') or {}).get('trigger_current') is not None))
    calendar = []
    for facet in facets:
        window = facet.get('window') or {}
        if (facet.get('timing_mode') == 'CALENDAR' and window.get('start')
                and window.get('end', '') >= (facet.get('published_at') or '')[:10]
                and FORWARD.search(window.get('original') or '')):
            calendar.append(facet)
    return max(calendar, key=lambda f: (f.get('published_at') or '',
               {'exact': 3, 'month': 2, 'quarter': 1}.get((f.get('window') or {}).get('precision'), 0))) if calendar else None


def repair_promoted_timings(conn):
    """Rebuild active timing from archived primary facets; retain the audit trail."""
    repaired = []
    for row in conn.execute("SELECT id,payload FROM events WHERE kind='live' AND id LIKE 'AUTO-%'"):
        event = json.loads(row['payload'])
        state = db.event_state(conn, row['id']) or {}
        if is_resolved(state.get('status', '')):
            continue
        facet = _best_facet(event)
        if not facet:
            continue
        candidate_row = conn.execute('SELECT * FROM discovery_candidates WHERE promoted_event_id=?', (row['id'],)).fetchone()
        candidate = dict(candidate_row) if candidate_row else None
        if not candidate:
            continue
        current_window = event.get('verified_window') or {}
        if (event.get('timing_mode') != 'EVENT_DRIVEN'
                and current_window.get('end', '') >= (event.get('guidance_published_at') or '')[:10]
                and FORWARD.search(current_window.get('original') or '')):
            continue
        original = (facet.get('window') or {}).get('original') or ''
        changed = False
        if facet.get('timing_mode') == 'EVENT_DRIVEN':
            trigger = facet['trigger']
            target = trigger['trigger_target']
            if event.get('trigger_target') != target or event.get('timing_mode') != 'EVENT_DRIVEN':
                event.update(trigger)
                event['timing_mode'] = 'EVENT_DRIVEN'
                event['verified_window'] = None
                changed = True
            progress = []
            linked_sources = {f.get('source_url') for f in event.get('timing_facets') or []
                              if (f.get('trigger') or {}).get('trigger_target') == target}
            statements = [json.loads(evidence['payload']) for evidence in conn.execute(
                'SELECT payload FROM catalyst_evidence WHERE ticker=?', (event['ticker'],))]
            linked_sources.update(statement.get('source_url') for statement in statements
                                  if statement.get('trigger_target') == target
                                  and candidate_matches_statement(candidate, statement.get('statement') or '')[0])
            for statement in statements:
                if (statement.get('timing_mode') != 'EVENT_DRIVEN'
                        or statement.get('trigger_target') != target
                        or statement.get('trigger_current') is None
                        or statement.get('reliability') == 'secondary'
                        or not (statement.get('source_url') in linked_sources
                                or _same_program(candidate, statement.get('statement') or '', original))):
                    continue
                progress.append(statement)
            if progress:
                latest = max(progress, key=lambda x: x.get('published_at') or '')
                if (event.get('trigger_current') is None or
                        (latest.get('published_at') or '') >= (event.get('trigger_as_of') or '')):
                    if event.get('trigger_current') != latest['trigger_current']:
                        changed = True
                    event.update(trigger_current=latest['trigger_current'],
                                 trigger_as_of=_progress_as_of(latest),
                                 monitoring_state=latest.get('monitoring_state'),
                                 trigger_source_url=latest.get('source_url'),
                                 trigger_source_id=latest.get('source_id'),
                                 trigger_quote=latest.get('statement'))
                    db.add_event_source(conn, row['id'], latest.get('source_id') or latest['source_url'],
                                        'sec', latest.get('source_url'), latest.get('published_at'),
                                        latest.get('statement'), False)
            inferred_type = ('P3_TOPLINE' if 'PHASE3' in (candidate.get('phase') or '') else
                             'P2_TOPLINE' if 'PHASE2' in (candidate.get('phase') or '') else event.get('type'))
            if event.get('type') != inferred_type:
                event['type'] = inferred_type
                changed = True
        else:
            window = facet['window']
            if event.get('verified_window') != window or event.get('timing_mode') != 'CALENDAR':
                event['verified_window'] = window
                event['timing_mode'] = 'CALENDAR'
                changed = True
            if state.get('status') == 'SCHEDULED' and window.get('precision') != 'exact':
                db.upsert_event_state(conn, row['id'], status='VERIFIED',
                                      verification_state=state.get('verification_state') or 'VERIFIED',
                                      verification_confidence=state.get('verification_confidence') or 0,
                                      event_timestamp=state.get('event_timestamp'),
                                      event_session=state.get('event_session') or 'unknown',
                                      note=state.get('note'))
                changed = True
        if changed:
            event['guidance_published_at'] = facet.get('published_at')
            event['provenance'].update(source_url=facet.get('source_url'),
                                       published_at=facet.get('published_at'), quote=original,
                                       date_precision=(facet.get('window') or {}).get('precision'),
                                       trigger_precision=(facet.get('trigger') or {}).get('trigger_precision'))
            with conn:
                conn.execute('UPDATE events SET payload=? WHERE id=?', (json.dumps(event, ensure_ascii=False), row['id']))
            repaired.append(row['id'])
    return repaired
