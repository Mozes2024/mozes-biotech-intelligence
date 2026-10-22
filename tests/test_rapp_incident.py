import json
from pathlib import Path
from mozes import db
from mozes.edge_sync import build_universe
from mozes.ingest import edgar, edgar_latest
from mozes.priority import priority_tickers
from mozes.materiality import classify_outcome

CASE=json.loads((Path(__file__).parent/'fixtures/rapp_20261020.json').read_text())


def test_rapp_qualifies_before_watchlist_promotion_and_exhibit_is_material_idempotently(tmp_path,monkeypatch):
    conn=db.connect(tmp_path/'offline.db')
    conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',
        ('rapport',CASE['company'],'RAPP',CASE['cik'],.9,'SEC-v2C-equity','2026-10-06T06:32:14Z'))
    conn.execute('INSERT INTO discovery_candidates(candidate_id,nct_id,sponsor,ticker,phase,status,raw_json,discovered_at) VALUES(?,?,?,?,?,?,?,?)',
        ('rapp-fixture','NCT07046494','Rapport Therapeutics Inc.','RAPP','PHASE2','ACTIVE_NOT_RECRUITING','{}','2026-10-06T06:32:14Z'))
    assert 'RAPP' not in priority_tickers() and len(priority_tickers())==39
    assert not conn.execute("SELECT 1 FROM watch_universe WHERE ticker='RAPP'").fetchone()
    assert edgar_latest.biotech_universe(conn)[CASE['cik']]['ticker']=='RAPP'
    assert any(r['ticker']=='RAPP' for r in build_universe(conn)['issuers'])
    # Reconstructed Atom envelope uses SEC metadata; not a claimed captured polling window.
    atom=f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>8-K - {CASE['company']} (0002012593) (Filer)</title>
      <id>urn:tag:sec.gov:accession-number={CASE['accession']}</id>
      <updated>{CASE['accepted_at']}</updated><link href="{CASE['index_url']}"/>
      </entry></feed>'''
    base=CASE['source_url'].rsplit('/',1)[0]+'/'
    def document(url,**kwargs):
        if url==CASE['index_url']:
            return json.dumps({'directory':{'item':[{'name':name} for name in CASE['documents']]}})
        assert url==CASE['source_url']
        return '<h1>'+CASE['headline']+'</h1><p>'+CASE['excerpt']+'</p>'
    monkeypatch.setattr(edgar,'_get',document)
    first=edgar_latest.poll_latest(conn,fetch=lambda _:atom,document_fetch=document,forms=('8-K',))
    assert first['material']==1 and first['errors']==0
    change=conn.execute("SELECT * FROM change_events WHERE ticker='RAPP'").fetchone()
    assert change['source_url']==CASE['source_url']
    assert json.loads(change['new_value'])['outcome']['polarity']=='positive'
    assert edgar_latest.poll_latest(conn,fetch=lambda _:atom,document_fetch=document,forms=('8-K',))['material']==0
    assert conn.execute('SELECT count(*) FROM change_events').fetchone()[0]==1
    assert conn.execute('SELECT count(*) FROM alert_deliveries').fetchone()[0]==0


def test_negative_phase2_control_is_material_without_relabeling_the_real_event():
    result=classify_outcome('Synthetic control: Phase 2 trial did not meet its primary endpoint.')
    assert result['material'] is True and result['polarity']=='negative'
