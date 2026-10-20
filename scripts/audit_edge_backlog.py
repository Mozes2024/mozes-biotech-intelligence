"""One fixed SELECT against D1. No recovery, processing, ACK or delivery calls."""
import json
import os
import urllib.request
from datetime import datetime, timezone

URL = 'https://api.cloudflare.com/client/v4/accounts/75b91ca20508a72979cb5337ce1cc7e2/d1/database/9baf99f2-1ccc-4415-affd-2708a7210e7b/query'
SQL = ('SELECT event_id,source,ticker,first_seen_at,analysis_status,analysis_attempts,'
       'analysis_retry_at,last_analysis_error,analysis_completed_at,material,'
       'enrichment_attempts,enrichment_retry_at,last_enrichment_error,'
       'enrichment_dispatch_at,enrichment_queued_at,enrichment_ack_at,'
       'enrichment_github_run_id FROM edge_events ORDER BY first_seen_at,event_id LIMIT 1001')


def inspect(*, token, opener=urllib.request.urlopen):
    # D1's query endpoint uses POST even for this constant read-only SELECT.
    request = urllib.request.Request(URL, data=json.dumps({'sql': SQL}).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with opener(request, timeout=30) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('audit response too large')
    result = json.loads(raw)
    if not result.get('success') or len(result.get('result', [])) != 1 or not result['result'][0].get('success'):
        raise ValueError('read-only backlog query failed')
    rows = result['result'][0]['results']
    if len(rows) >= 1001:
        raise ValueError('backlog audit bound exceeded')
    return {'read_only': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'events': rows}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect(token=os.environ['CLOUDFLARE_RECOVERY_READ_TOKEN'])))
    except (OSError, KeyError, ValueError):
        raise SystemExit('Read-only backlog audit unavailable; no writes attempted') from None
