"""Bounded read-only evidence for one reported missed accession; no monitor DB."""
import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = 'https://www.sec.gov/Archives/edgar/data/2012593/000119312526425647/'
OUT = Path('.diagnostics/rapp')
QUERIES = [
    "SELECT * FROM edge_issuers WHERE ticker='RAPP' OR cik='2012593'",
    "SELECT * FROM edge_events WHERE accession='0001193125-26-425647' OR ticker='RAPP'",
    "SELECT * FROM edge_candidates WHERE ticker='RAPP' OR ticker_hint='RAPP' OR headline LIKE '%Rapport%'",
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    proof = {'checked_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'sources':[]}
    def fetch(url, name):
        row={'url':url,'file':name}
        proof['sources'].append(row)
        try:
            req=urllib.request.Request(url,headers={'User-Agent':os.environ['SEC_USER_AGENT']})
            with urllib.request.urlopen(req,timeout=20) as response:
                row.update(status=response.status,final_url=response.geturl())
                if response.geturl().split('/')[2] not in ('www.sec.gov','data.sec.gov'):
                    raise ValueError('SEC provenance changed')
                raw=response.read(4_000_001)
            if len(raw)>4_000_000:
                raise ValueError('SEC response too large')
            (OUT/name).write_bytes(raw)
            row['sha256']=hashlib.sha256(raw).hexdigest()
            return raw
        except (OSError,ValueError) as exc:
            row['error']=f'{type(exc).__name__}: {exc}'[:160]
            return None
    fetch(BASE+'d185134d8k.htm','filing.htm')
    fetch(BASE+'0001193125-26-425647-index.htm','index.htm')
    fetch(BASE+'0001193125-26-425647.txt','submission.txt')
    directory=fetch(BASE+'index.json','directory.json')
    if directory:
        names=[r['name'] for r in json.loads(directory)['directory']['item']
               if ('ex99' in r['name'].lower() or 'exhibit99' in r['name'].lower())
               and r['name'].lower().endswith(('.htm','.html'))]
        for name in names[:3]:
            if '/' in name or '..' in name:
                raise ValueError('invalid exhibit name')
            fetch(BASE+name,name)
    fetch('https://data.sec.gov/submissions/CIK0002012593.json','submissions.json')
    token=os.environ.get('CLOUDFLARE_RECOVERY_READ_TOKEN')
    if token:
        url='https://api.cloudflare.com/client/v4/accounts/75b91ca20508a72979cb5337ce1cc7e2/d1/database/9baf99f2-1ccc-4415-affd-2708a7210e7b/query'
        try:
            req=urllib.request.Request(url,data=json.dumps({'sql':';'.join(QUERIES)}).encode(),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=30) as response:
                raw=response.read(2_000_001)
            if len(raw)>2_000_000 or not json.loads(raw).get('success'):
                raise ValueError('private evidence unavailable')
            (OUT/'edge-evidence.json').write_bytes(raw)
            proof['edge_evidence']='saved; SELECT only'
        except (OSError,ValueError):
            proof['edge_evidence']='unavailable; no writes attempted'
    (OUT/'provenance.json').write_text(json.dumps(proof,indent=2))
    print(json.dumps(proof))


if __name__=='__main__':
    main()
