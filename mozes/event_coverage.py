"""Read-only source capabilities for each event; freshness is not verification."""
from datetime import datetime, timezone, timedelta


def matrix(events, sources, *, now=None):
    now = now or datetime.now(timezone.utc)
    rows = []
    for event in events:
        ticker = event.get('ticker')
        matching = [s for s in sources if s.get('ticker') == ticker or s.get('source') == 'official_feed:'+str(ticker)]
        evidence = []
        for source in matching:
            try:
                stamp = datetime.fromisoformat((source.get('last_success_at') or '').replace('Z','+00:00'))
                age = now-stamp
                fresh = timedelta(0) <= age <= timedelta(minutes=30)
            except (ValueError,TypeError):
                fresh = False
            evidence.append({'source':source.get('source'), 'source_url':source.get('source_url'),
                'status':source.get('status'), 'last_success_at':source.get('last_success_at'),
                'fresh':fresh, 'alternate':source.get('covered_by'),
                'capability':'company_disclosures', 'verifies_event':False})
        rows.append({'event_id':event.get('id'), 'ticker':ticker,'sources':evidence,
                     'needs_collection':not any(s['fresh'] for s in evidence)})
    return sorted(rows,key=lambda r:(not r['needs_collection'],str(r['ticker']),str(r['event_id'])))
