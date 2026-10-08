import json
from pathlib import Path
from datetime import datetime, timezone
import pytest
from mozes import db
from mozes.clinical_events import classify_clinical, ingest_clinical, clinical_payload, investigate, catalyst_window, retry_unresolved

CASES=json.loads((Path(__file__).parent/'fixtures/clinical_announcements.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('case',CASES,ids=lambda c:c['id'])
def test_clinical_classification(case):
    result=classify_clinical(case['headline']+'\n'+case['summary'])
    for key,value in case['expected'].items(): assert result[key]==value,(case['id'],key,result)

def mapped(conn,ticker='ZZZZ',company='Novel Bio',cik='123'):
    conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',(company.lower(),company,ticker,cik,.99,'SEC-v2C-equity','2026-10-08'))
    conn.commit()

def ingest(conn,case,**overrides):
    return ingest_clinical(conn,headline=case['headline'],summary=case['summary'],
        source_url=overrides.pop('source_url',case.get('source_url','https://www.businesswire.com/news/'+case['id'])),
        published_at='2026-10-05T20:05:00Z',source_type=overrides.pop('source_type','wire'),**overrides)

def test_vir_creates_catalyst_not_positive_result_and_preserves_source(tmp_path):
    conn=db.connect(tmp_path/'c.db'); mapped(conn,'VIR','Vir Biotechnology, Inc.','1706431')
    result=ingest(conn,CASES[0],source_type='company_ir')
    row=clinical_payload(conn)['catalysts'][0]
    assert row['ticker']=='VIR' and row['window']['start']=='2026-11-08'
    assert row['window']['precision']=='exact' and row['program']=='SOLSTICE'
    assert row['sources'][0]['source_url']==CASES[0]['source_url']
    assert result['scope']['polarity']=='unknown'
    payload=json.loads(conn.execute('SELECT payload_json FROM alert_outbox').fetchone()[0])
    assert payload['priority']=='P2' and payload['outcome']['actionable'] is False
    assert [r[0] for r in conn.execute('SELECT lifecycle FROM clinical_event_history ORDER BY sequence')]==['DISCOVERED','RESOLVED','CLASSIFIED','ENRICHED','PUBLISHED']

def test_quarter_is_not_a_precise_readout_date():
    window=catalyst_window(CASES[3]['summary'],'2026-10-05T20:05:00Z')
    assert (window['start'],window['end'],window['precision'])==('2026-10-01','2026-12-31','quarter')

def test_unresolved_can_promote_after_verified_evidence_without_ticker_hardcoding(tmp_path):
    conn=db.connect(tmp_path/'c.db');case=CASES[6]
    first=ingest(conn,case)
    assert first['change_id'] is None and clinical_payload(conn)['operations']['suppressed_by_reason']=={'issuer_unresolved':1}
    assert conn.execute('SELECT status FROM issuer_discovery_queue').fetchone()[0]=='pending'
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0]==0
    mapped(conn)
    retry_unresolved(conn)
    assert clinical_payload(conn)['catalysts'][0]['ticker']=='ZZZZ'
    assert conn.execute("SELECT cik FROM watch_universe WHERE ticker='ZZZZ'").fetchone()[0]=='123'
    assert investigate(conn,'Novel Bio')[0]['lifecycle']=='PUBLISHED'

def test_ambiguous_company_and_symbols_never_resolve(tmp_path):
    conn=db.connect(tmp_path/'c.db');mapped(conn);mapped(conn,'CCCC','Collision Bio','456')
    result=ingest(conn,CASES[7])
    assert result['change_id'] is None and not clinical_payload(conn)['catalysts']

def test_sources_share_one_catalyst_and_one_alert(tmp_path):
    conn=db.connect(tmp_path/'c.db');mapped(conn)
    for source,host in [('sec','www.sec.gov'),('wire','www.businesswire.com'),('wire','www.globenewswire.com')]:
        ingest(conn,CASES[8],source_type=source,source_url='https://'+host+'/news/sunrise')
    assert len(clinical_payload(conn)['catalysts'])==1
    assert len(clinical_payload(conn)['catalysts'][0]['sources'])==3
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0]==1

def test_routine_and_management_record_suppression_without_urgent_alerts(tmp_path):
    conn=db.connect(tmp_path/'c.db');mapped(conn)
    for case in CASES[4:6]:ingest(conn,case)
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0]==0
    reasons=clinical_payload(conn)['operations']['suppressed_by_reason']
    assert reasons=={'routine_conference':1,'outside_scope':1}

def test_changed_verified_evidence_promotes_and_retries_do_not_write_or_notify_twice(tmp_path):
    conn=db.connect(tmp_path/'c.db');mapped(conn)
    first=ingest(conn,{**CASES[9],'summary':'Novel Bio (Nasdaq: ZZZZ) will present a company update.'})
    assert first['change_id'] is None
    second=ingest(conn,CASES[9]);before=conn.total_changes
    third=ingest(conn,CASES[9])
    assert second['change_id']==third['change_id'] and conn.total_changes==before
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0]==1


def test_rfc_publication_and_reconcile_receipt_are_durable(tmp_path):
    from mozes.clinical_reconcile import process_candidates
    conn=db.connect(tmp_path/'c.db');mapped(conn)
    candidate={'candidate_id':'EDGE-'+'a'*24,'source_url':'https://www.businesswire.com/news/new',
               'headline':CASES[3]['headline'],'summary':CASES[3]['summary'],
               'published_at':'Mon, 05 Oct 2026 20:05:00 GMT'}
    receipts=process_candidates(conn,[candidate],github_run_id='42')
    assert receipts[0]['lifecycle']=='PUBLISHED'
    assert conn.execute('SELECT published_at FROM clinical_events').fetchone()[0]=='2026-10-05T20:05:00+00:00'
    assert conn.execute('SELECT producer_run_id FROM edge_candidate_receipts').fetchone()[0]=='42'
    process_candidates(conn,[candidate],github_run_id='43')
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0]==1
    candidate['published_at']='malformed'
    assert process_candidates(conn,[candidate],github_run_id='44')[0]['suppression_reason']=='classification_uncertain'
    candidate['source_url']='https://www.businesswire.com.attacker.example/news/new'
    with pytest.raises(ValueError,match='untrusted'):process_candidates(conn,[candidate],github_run_id='45')


def test_generic_sec_title_uses_exact_cik_path_and_keeps_quarter(tmp_path):
    from mozes.live_monitor import record_change
    from mozes.clinical_events import source_issuer
    conn=db.connect(tmp_path/'c.db');mapped(conn)
    url='https://www.sec.gov/Archives/edgar/data/123/000000012326000001/ex99.htm'
    assert source_issuer(conn,'ZZZZ','sec',url)
    assert source_issuer(conn,'ZZZZ','sec',url.replace('/123/','/456/')) is None
    record_change(conn,ticker='ZZZZ',change_type='sec_material_filing',previous_value=None,
                  new_value={'headline':'Phase 3 SUNRISE topline data expected in Q4 2026','published_at':'2026-10-05'},source_type='sec',source_url=url)
    assert clinical_payload(conn)['catalysts'][0]['window']['precision']=='quarter'


def test_retention_bounds_audit_but_keeps_catalyst_provenance(tmp_path):
    from mozes.clinical_events import retain_candidates
    conn=db.connect(tmp_path/'c.db');mapped(conn)
    ingest(conn,CASES[3])
    conn.execute("UPDATE clinical_events SET first_seen_at='2026-01-01'");conn.commit()
    retain_candidates(conn)
    assert conn.execute('SELECT COUNT(*) FROM clinical_events').fetchone()[0]==0
    assert clinical_payload(conn)['catalysts'][0]['sources']


def test_confirmed_endpoint_is_urgent_despite_future_secondary_readout():
    result=classify_clinical('Novel Bio Phase 2 SUNRISE met its primary endpoint. Additional data expected in Q4 2026.')
    assert result['actionable'] and result['polarity']=='positive' and not result['catalyst']
    scheduled=classify_clinical('Novel Bio will present positive Phase 2 SUNRISE results at ASCO. Expanded clinical data scheduled for November 2026.')
    assert scheduled['catalyst'] and not scheduled['actionable'] and scheduled['polarity']=='unknown'


def test_readout_timing_update_changes_one_card_without_fabricating_date(tmp_path):
    conn=db.connect(tmp_path/'c.db');mapped(conn)
    first=ingest(conn,CASES[3])
    update={'id':'timing_update','headline':'Novel Bio delays Phase 3 SUNRISE topline readout to Q1 2027',
            'summary':'Novel Bio (Nasdaq: ZZZZ) now expects Phase 3 SUNRISE results in Q1 2027.'}
    second=ingest(conn,update,source_url='https://www.businesswire.com/news/update')
    assert first['catalyst_id']==second['catalyst_id']
    row=clinical_payload(conn)['catalysts'][0]
    assert row['window']['precision']=='quarter' and row['window']['end']=='2027-03-31'


@pytest.mark.parametrize('headline',[
    'Novel Bio will report Phase 1 interim analysis with new clinical data in December 2026',
    'Novel Bio expects Phase 3 enrollment completion in Q4 2026',
    'Novel Bio announces PDUFA date scheduled in Q1 2027',
    'Novel Bio AdCom meeting scheduled for November 8, 2026',
    'Novel Bio plans to submit an NDA in Q4 2026',
    'Novel Bio expects Phase 3 trial completion in Q4 2026',
])
def test_broad_upcoming_clinical_and_regulatory_milestones(headline):
    scope=classify_clinical(headline)
    assert scope['relevant'] and scope['material'] and scope['catalyst'] and not scope['actionable']
    assert scope['polarity']=='unknown'


def test_dashboard_renders_quarter_uncertainty_and_safe_source_links():
    import subprocess
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('web/live_intelligence_ui.js','utf8');
const start=s.indexOf('function clinicalCatalystsHtml()'),end=s.indexOf('let currentEdgeHealth',start);
const context={URL,D:{clinical_intelligence:{catalysts:[{ticker:'ZZZZ',headline:'<script>bad</script>',window:{start:'2026-10-01',end:'2026-12-31',precision:'quarter'},sources:[{source_url:'javascript:bad'},{source_url:'https://issuer.example/news',published_at:'2026-10-05'}]}]}},esc:x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')};
vm.createContext(context);vm.runInContext(s.slice(start,end),context);
const html=context.clinicalCatalystsHtml();
assert(html.includes('2026-10-01 — 2026-12-31 (quarter)'));assert(!html.includes('<script>'));
assert(!html.includes('javascript:'));assert(html.includes('2026-10-05'));assert(html.includes('כיוון התוצאות עדיין לא ידוע'));
'''
    result=subprocess.run(['node','-e',script],cwd=Path(__file__).parents[1],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_rss_replay_recovers_october_fifth_outside_old_twelve_hour_window(tmp_path):
    from mozes.primary_feeds import _parse_rss_items, poll_wire_feeds
    from xml.sax.saxutils import escape
    case=CASES[0]
    rss=('<rss><channel><item><title>'+escape(case['headline'])+'</title><description>'+escape(case['summary'])+'</description><link>'+case['source_url']+'</link><pubDate>Mon, 05 Oct 2026 20:05:00 GMT</pubDate></item></channel></rss>').encode()
    now=datetime(2026,10,8,12,tzinfo=timezone.utc)
    assert not _parse_rss_items(rss,now=now,max_age_hours=12)
    conn=db.connect(tmp_path/'c.db');mapped(conn,'VIR','Vir Biotechnology, Inc.','1706431')
    poll_wire_feeds(conn,fetch=lambda _:rss,now=now)
    assert len(clinical_payload(conn)['catalysts'])==1
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0]==1


def test_partner_search_never_assigns_vir_announcement_to_alny(tmp_path,monkeypatch):
    from mozes import news_signals
    from xml.sax.saxutils import escape
    conn=db.connect(tmp_path/'c.db');mapped(conn,'VIR','Vir Biotechnology, Inc.','1706431')
    mapped(conn,'ALNY','Alnylam Pharmaceuticals, Inc.','1178670')
    db.upsert_watch(conn,'ALNY',company='Alnylam Pharmaceuticals, Inc.',cik='1178670',source='dynamic_news_discovery')
    monkeypatch.setattr(news_signals,'discover_news_issuers',lambda *a,**k:({'signals_seen':0},[]))
    monkeypatch.setattr(news_signals,'poll_official_feeds',lambda *a,**k:{})
    case=CASES[0]
    rss=('<rss><channel><item><title>'+escape(case['headline'])+'</title><link>https://news.google.com/rss/articles/regression</link><source url="https://www.businesswire.com">Business Wire</source><pubDate>Mon, 05 Oct 2026 20:05:00 GMT</pubDate></item></channel></rss>').encode()
    news_signals.poll_news(conn,fetch=lambda _:rss,now=datetime(2026,10,6,13,tzinfo=timezone.utc))
    assert [r[0] for r in conn.execute('SELECT ticker FROM change_events')]==['VIR']
    assert not clinical_payload(conn)['catalysts']  # Secondary discovery is not official verification.
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0]==0
    retry_unresolved(conn)
    assert not clinical_payload(conn)['catalysts']


def test_policy_upgrade_hides_old_false_catalyst_without_deleting_evidence(tmp_path,monkeypatch):
    import mozes.clinical_events as clinical
    conn=db.connect(tmp_path/'c.db');mapped(conn,'VIR','Vir Biotechnology, Inc.','1706431')
    case=CASES[10]
    old_future=clinical.RULES['future'];old_version=clinical.RULES['version']
    monkeypatch.setitem(clinical.RULES,'future',r'\btargeted\b')
    monkeypatch.setitem(clinical.RULES,'version','clinical-v1')
    first=ingest(conn,case)
    assert first['catalyst_id'] and clinical_payload(conn)['catalysts']
    monkeypatch.setitem(clinical.RULES,'future',old_future)
    monkeypatch.setitem(clinical.RULES,'version',old_version)
    clinical.reclassify_policy(conn)
    assert not clinical_payload(conn)['catalysts']
    assert conn.execute('SELECT COUNT(*) FROM clinical_catalysts').fetchone()[0]==1
    assert conn.execute('SELECT COUNT(*) FROM clinical_catalyst_sources').fetchone()[0]==1
