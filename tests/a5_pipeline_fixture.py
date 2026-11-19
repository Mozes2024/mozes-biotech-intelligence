"""Offline producer fixture for the real browser script-chain regression."""
import hashlib
import json
import tempfile
from pathlib import Path

from mozes import db
from mozes.alert_dispatch import recent_alerts
from mozes.alert_feed import build_feed
from mozes.alert_explanation import enrich_pending
from mozes.live_monitor import record_change


def fixture():
    with tempfile.TemporaryDirectory() as directory:
        conn = db.connect(Path(directory) / 'test.db')
        conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',
                     ('novel', 'Novel Bio', 'ZZZZ', '123', .99, 'SEC-v2C-equity', '2026-10-12'))
        db.upsert_security_lifecycle(conn, 'ZZZZ', status='ACTIVE', source_url='https://www.sec.gov/files/company_tickers_exchange.json')
        documents = {}
        cases = [
            ('approval', 'FDA approved the NDA for DrugX to treat DiseaseY.'),
            ('failure', 'The trial failed to meet its primary endpoint.'),
            ('milestone', 'FDA accepted the NDA for review.'),
            ('cvr', 'No product of the company has been approved by FDA. Under the CVR Agreement, payments occur only if FDA approval is granted in the future.'),
            ('mixed', 'DrugA met its primary endpoint. DrugB failed to meet its primary endpoint.'),
        ]
        ids = {}
        for name, text in cases:
            url = 'https://www.sec.gov/Archives/edgar/data/123/' + name + '.htm'
            documents[url] = '<article><p>' + text + '</p><p>Read the complete source announcement for the program, population and trial design. These test documents contain no investment recommendation.</p></article>'
            ids[name] = record_change(conn, ticker='ZZZZ', change_type='sec_material_filing', severity='high',
                previous_value=None, new_value={'headline': name, 'summary': text}, source_type='sec',
                source_url=url, source_hash=hashlib.sha256(documents[url].encode()).hexdigest(), verification_state='primary_source',identity=['a5-test',name])
        # Simulated immutable legacy payload: exporting must not repair or reclassify it.
        legacy = {'priority':'P1', 'ticker':'ATOS', 'watched':True, 'change_type':'sec_material_filing',
                  'source_type':'sec','source_url':'https://www.sec.gov/legacy.htm',
                  'new_value':{'headline':'Legacy CVR filing'},'outcome':{'polarity':'positive'}}
        cid = 'CHG-' + 'a' * 24
        conn.execute('INSERT INTO alert_outbox(alert_id,change_id,stage,channel,status,attempts,created_at,send_after,payload_json) VALUES(?,?,?,?,?,?,?,?,?)',
                     ('ALT-'+'b'*24,cid,'stage1','log','sent',1,db.utcnow(),db.utcnow(),json.dumps(legacy)))
        conn.commit()
        immutable = list(conn.execute('SELECT alert_id,payload_json FROM alert_outbox ORDER BY alert_id'))
        enrich_pending(conn, limit=5, fetch=lambda url, _: documents[url])
        feed = build_feed(conn)
        assert [(r[0],r[1]) for r in immutable] == [tuple(r) for r in conn.execute('SELECT alert_id,payload_json FROM alert_outbox ORDER BY alert_id')]
        assert set(ids.values()) <= {r['change_id'] for r in recent_alerts(conn)}
        feed['sequence'] = 1
        conn.close()
        return {'feed':feed,'ids':ids,'legacy_id':cid}


if __name__ == '__main__':
    print(json.dumps(fixture(),ensure_ascii=True))
