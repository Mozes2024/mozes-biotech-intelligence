"""Bounded Edge discovery queue reconciliation, ACK only after artifact upload."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

from . import db
from .config import DB_PATH
from .clinical_events import ingest_clinical, publication_timestamp
from .edge_enrichment import edge_request


def process_candidates(conn, candidates, *, github_run_id, limit=10):
    if not re.fullmatch(r'\d{1,20}',github_run_id):raise ValueError('verified producer run required')
    receipts=[]
    for candidate in candidates[:limit]:
        cid=candidate.get('candidate_id','')
        if not re.fullmatch(r'EDGE-[a-f0-9]{24}',cid):raise ValueError('invalid candidate ID')
        parts=urlsplit(candidate.get('source_url',''))
        if (parts.scheme!='https' or parts.hostname not in {'www.businesswire.com','businesswire.com','www.globenewswire.com','globenewswire.com','rss.globenewswire.com'}
                or parts.username or parts.port not in (None,443)):
            raise ValueError('untrusted discovery source')
        stamp=candidate.get('published_at')
        try:
            stamp=publication_timestamp(stamp) if stamp else None
        except (ValueError, TypeError, IndexError, AttributeError):
            stamp=None
        if not stamp:
            result={'event_id':None,'change_id':None,'catalyst_id':None}
            reason='classification_uncertain'
        else:
            result=ingest_clinical(conn,headline=candidate['headline'],summary=candidate['summary'],source_url=parts.geturl(),
                published_at=stamp,source_type='wire',edge_event_id=cid)
            row=conn.execute('SELECT * FROM clinical_events WHERE event_id=?',(result['event_id'],)).fetchone()
            reason=row['suppression_reason']
            # Preserve the exact publisher entry used for classification; this is not fetched article HTML.
            content=json.dumps({'headline':candidate['headline'],'summary':candidate['summary'],'published_at':stamp},ensure_ascii=False)
            digest=hashlib.sha256(content.encode()).hexdigest()
            source_id=cid+'-RSS-'+digest
            if not conn.execute('SELECT 1 FROM source_archive WHERE source_id=?',(source_id,)).fetchone():
                db.archive_source(conn,source_id,parts.geturl(),'wire',stamp,db.utcnow(),content,{'format':'RSS entry snapshot','edge_candidate_id':cid})
        receipt={'candidate_id':cid,'github_run_id':github_run_id,'lifecycle':'PUBLISHED' if result.get('change_id') else 'SUPPRESSED',
                 'suppression_reason':reason,'change_id':result.get('change_id'),'catalyst_id':result.get('catalyst_id'),
                 'ticker':row['ticker'] if stamp else None,'cik':row['cik'] if stamp else None}
        receipt['content_hash']=candidate.get('content_hash')
        with conn:
            conn.execute('INSERT INTO edge_candidate_receipts VALUES(?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET producer_run_id=excluded.producer_run_id,receipt_json=excluded.receipt_json,processed_at=excluded.processed_at',
                         (cid,github_run_id,json.dumps(receipt),db.utcnow()))
        receipts.append(receipt)
    return receipts


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('process','ack'));args=parser.parse_args()
    if not os.environ.get('MOZES_EDGE_SYNC_URL'):
        print(json.dumps({'status':'SKIPPED','reason':'Edge not configured'}));return
    path=Path(os.environ.get('MOZES_DB_PATH',DB_PATH));run_id=os.environ['GITHUB_RUN_ID']
    if args.action=='ack':
        checkpoint=json.loads(path.with_name('edge-checkpoint.json').read_text())
        if checkpoint.get('producer_run_id')!=run_id or checkpoint.get('db_sha256')!=hashlib.sha256(path.read_bytes()).hexdigest():
            raise RuntimeError('candidate ACK requires uploaded database checkpoint')
    conn=db.connect(path)
    try:
        if args.action=='process':
            try:
                response=edge_request('candidates')
                if response.get('service')=='mozes-hot-clock' and 'candidates' not in response:
                    print(json.dumps({'status':'WAITING_DEPLOYMENT'}));return
                rows=response['candidates']
            except HTTPError as error:
                if error.code==404:
                    print(json.dumps({'status':'WAITING_DEPLOYMENT'}));return
                raise
            receipts=process_candidates(conn,rows,github_run_id=run_id)
            print(json.dumps({'status':'OK','processed':len(receipts)}))
        else:
            rows=conn.execute('SELECT receipt_json FROM edge_candidate_receipts WHERE producer_run_id=? LIMIT 10',(run_id,)).fetchall()
            for row in rows:edge_request('candidate/ack',payload=json.loads(row[0]))
            print(json.dumps({'status':'OK','acknowledged':len(rows)}))
    finally:conn.close()


if __name__=='__main__':main()
