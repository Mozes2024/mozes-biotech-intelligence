"""Frozen observations: absence never becomes observed failure or a gate shortcut."""
from copy import deepcopy
from pathlib import Path
import json
import math
import pytest
from mozes import engine_v2 as engine
from mozes.scoring_common import known_number, risk_flags

FIXTURES=json.loads((Path(__file__).parent/'fixtures/missing_evidence.json').read_text(encoding='utf-8-sig'))
STATE={'status':'VERIFIED','verification_state':'VERIFIED'}
TODAY='2026-10-12'


def analyze(event, *, open_gates=False, financial=True):
    evidence=engine.evidence_for(event)
    if financial:
        runway=event.get('runway_months')
        evidence['financial_information_status']='KNOWN' if known_number(runway) and runway>=0 else 'MISSING'
    flags=risk_flags(event)
    gates={k:{'satisfied':open_gates} for k in ('hold_through','runup')}
    classification=engine.classify_v2(event,STATE,{'score':90},evidence,flags,{'available':True},100,gates,TODAY,None)
    recommendation=engine.recommendation_status(STATE,{'score':90},evidence,flags,{'available':True},classification,gates)
    return evidence,classification,recommendation


@pytest.mark.parametrize('name',['unknown_clinical','unknown_regulatory','absent_prior'])
@pytest.mark.parametrize('open_gates',[False,True])
def test_unknown_is_information_gap_not_negative_or_positive(name,open_gates):
    event=deepcopy(FIXTURES[name]);before=deepcopy(event)
    evidence,classification,recommendation=analyze(event,open_gates=open_gates)
    assert evidence['score'] is None
    assert evidence['negative_evidence'] is False
    assert classification['class']=='INSUFFICIENT_INFORMATION' and not classification['actionable']
    assert recommendation['status']=='INSUFFICIENT_INFORMATION'
    assert event==before


@pytest.mark.parametrize('name',['negative_clinical','negative_regulatory'])
@pytest.mark.parametrize('open_gates',[False,True])
def test_observed_negative_survives_missing_other_evidence(name,open_gates):
    evidence,classification,recommendation=analyze(FIXTURES[name],open_gates=open_gates)
    assert evidence['negative_evidence'] is True
    assert classification['class']=='AVOID_BINARY'
    assert recommendation['status']=='NOT_NOW'


@pytest.mark.parametrize('name',['positive_clinical','positive_regulatory'])
def test_known_positive_remains_research_candidate_without_unlocking_gates(name):
    evidence,classification,recommendation=analyze(FIXTURES[name])
    assert evidence['information_status']=='COMPLETE' and evidence['score']>=75
    assert classification['class']=='REVIEW'
    assert recommendation['status']=='INVESTMENT_CANDIDATE'
    assert recommendation['validation_gates_separate'] is True


@pytest.mark.parametrize('name',['partial_positive','partial_low_prior'])
@pytest.mark.parametrize('open_gates',[False,True])
def test_incomplete_positive_observations_do_not_infer_success_or_failure(name,open_gates):
    evidence,classification,recommendation=analyze(FIXTURES[name],open_gates=open_gates)
    assert evidence['information_status']=='PARTIAL' and evidence['negative_evidence'] is False
    assert classification['class']=='INSUFFICIENT_INFORMATION'
    assert recommendation['status']=='INSUFFICIENT_INFORMATION'


@pytest.mark.parametrize('score',[None,float('nan'),float('inf'),'unknown',False])
@pytest.mark.parametrize('open_gates',[False,True])
def test_null_safe_gate_handling_with_unknown_scores(score,open_gates):
    gates={k:{'satisfied':open_gates} for k in ('hold_through','runup')}
    classification=engine.classify_v2({'type':'P3_TOPLINE'},STATE,{'score':90},{'score':score},[],{'available':True},100,gates,TODAY,None)
    assert classification['class']=='INSUFFICIENT_INFORMATION'
    assert engine.recommendation_status(STATE,{'score':90},{'score':score},[],{},classification,gates)['status']=='INSUFFICIENT_INFORMATION'


@pytest.mark.parametrize('gates',[{},None,{'hold_through':None,'runup':None}])
def test_empty_evidence_and_missing_gates_fail_closed(gates):
    classification=engine.classify_v2({'type':'P3_TOPLINE'},STATE,None,None,None,None,None,gates,TODAY,None)
    assert classification['class']=='INSUFFICIENT_INFORMATION'


@pytest.mark.parametrize('runway',[None,'unknown',float('nan'),-1])
@pytest.mark.parametrize('open_gates',[False,True])
def test_missing_financial_data_does_not_become_risk_or_positive_recommendation(runway,open_gates):
    event={**FIXTURES['positive_clinical'],'runway_months':runway}
    _,classification,recommendation=analyze(event,open_gates=open_gates)
    assert all(f['code']!='short_cash_runway' for f in risk_flags(event))
    assert classification['class']=='INSUFFICIENT_INFORMATION'
    assert recommendation['status']=='INSUFFICIENT_INFORMATION'


def test_observed_cash_shortfall_retains_negative_recommendation():
    event={**FIXTURES['positive_clinical'],'runway_months':3}
    _,_,recommendation=analyze(event)
    assert any(f['code']=='short_cash_runway' for f in risk_flags(event))
    assert recommendation['status']=='NOT_NOW'


@pytest.mark.parametrize('features',[None,{}, {'prior':None,'n':None},
    {'prior':[],'design':{},'n':'unknown','integrity':'unknown','negative_adcom':'false'},
    {'safety_concern':'unknown','class_failures':'unknown','n':0}])
def test_unknown_feature_values_are_not_truthy_negative_observations(features):
    event={'type':'P2_TOPLINE','features':features}
    evidence,classification,recommendation=analyze(event)
    assert evidence['score'] is None and not risk_flags(event)
    assert classification['class']=='INSUFFICIENT_INFORMATION'
    assert recommendation['status']=='INSUFFICIENT_INFORMATION'


def test_information_gap_does_not_override_resolved_quarantined_or_stale():
    for state,window,expected in [({'status':'RESOLVED_SUCCESS'},None,'RESOLVED'),
                                 ({'status':'QUARANTINED'},None,'QUARANTINED'),
                                 (STATE,{'end':'2026-10-01'},'STALE_UNRESOLVED')]:
        result=engine.classify_v2({'type':'P3_TOPLINE'},state,{},None,[],{},None,{},TODAY,window)
        assert result['class']==expected


def test_pit_known_dates_filter_unknown_finances_without_mutating_snapshot():
    from mozes.pit import snapshot_at, thaw
    original={**deepcopy(FIXTURES['positive_clinical']), 'id':'PIT-FIX','features_as_of':'2026-10-01',
              'runway':{'months':24,'known_from':'2026-10-13'}}
    snap=snapshot_at(original,TODAY);before=thaw(snap)
    evidence,classification,recommendation=analyze(snap)
    assert evidence['information_status']=='COMPLETE'
    assert classification['class']=='INSUFFICIENT_INFORMATION'
    assert recommendation['status']=='INSUFFICIENT_INFORMATION'
    assert thaw(snap)==before and original['runway']['known_from']=='2026-10-13'
    original['features_as_of']='2026-10-13'
    late=snapshot_at(original,TODAY)
    assert engine.evidence_for(late)['score'] is None


def test_active_engine_preserves_database_sources_and_historical_evaluations(tmp_path):
    import hashlib
    from mozes import db
    from mozes.intelligence_store import ensure_schema
    path=tmp_path/'preserved.db';conn=db.connect(path);ensure_schema(conn)
    event={**deepcopy(FIXTURES['unknown_clinical']),'id':'ALT-FIX','ticker':'ZZZZ',
           'provenance':{'source_known_at':'2026-10-01T10:00:00+00:00'}}
    with conn:
        for eid in ('EDGE-FIX','CHG-FIX','CAT-FIX','ALT-FIX'):
            conn.execute('INSERT INTO events VALUES(?,?,?)',(eid,'live',json.dumps({**event,'id':eid})))
    db.upsert_event_state(conn,event['id'],status='VERIFIED',verification_state='VERIFIED',verification_confidence=100)
    db.add_event_source(conn,event['id'],'source-known','company_ir',published_at='2026-10-01T09:00:00Z',
                        retrieved_at='2026-10-01T10:00:00Z',statement='frozen source')
    db.store_score_run(conn,{'id':'ALT-FIX','as_of':'2026-10-01','versions':{'evidence':'frozen'},'historical_result':'preserve'})
    before=hashlib.sha256(path.read_bytes()).hexdigest();changes=conn.total_changes
    sources=db.load_event_sources(conn,event['id']);stored=list(conn.execute('SELECT * FROM score_runs'))
    conn.execute('PRAGMA query_only=ON')
    result=engine.score_event(conn,event,TODAY)
    assert result['classification']['class']=='INSUFFICIENT_INFORMATION'
    assert result['sources']==sources and result['provenance']==event['provenance']
    assert not result['gates']['runup']['satisfied'] and not result['gates']['hold_through']['satisfied']
    assert conn.total_changes==changes and list(conn.execute('SELECT * FROM score_runs'))==stored
    conn.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
