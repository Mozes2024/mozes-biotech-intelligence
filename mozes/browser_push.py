"""Bounded desktop push delivery at the existing single-producer publication point."""
from __future__ import annotations
import json
import os
import urllib.request
from .alert_feed import build_feed
from .config import DB_PATH
from . import db

URL = 'https://mozes-bio-push.sp500.workers.dev/publish'

def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('MOZES_PUSH_FROM_ACTIONS') != '1':
        print(json.dumps({'status':'DISABLED'}))
        return 0
    conn = db.connect(DB_PATH)
    try:
        request_url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL'] + '&audience=mozes-bio-push'
        request = urllib.request.Request(request_url, headers={'Authorization':'Bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
        with urllib.request.urlopen(request,timeout=10) as response:
            token=json.load(response)['value']
        body=json.dumps(build_feed(conn),ensure_ascii=False).encode()
        request=urllib.request.Request(URL,data=body,method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=20) as response:
            result=json.load(response)
        # Metadata only. No JWT, subscription endpoint, auth material or raw payload in logs.
        if result.get('error'):
            print(json.dumps({'status':'FAILED','error':'delivery-service-refused'}));return 1
        print(json.dumps({k:result[k] for k in ['status','eligible_new','push_accepted','push_failed','enrolled','pending'] if k in result}))
        return 1 if result.get('push_failed') else 0
    except Exception as exc:
        print(json.dumps({'status':'FAILED','error':type(exc).__name__}));return 1
    finally:
        conn.close()

if __name__=='__main__':
    raise SystemExit(main())
