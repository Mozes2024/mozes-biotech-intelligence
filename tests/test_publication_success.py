import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from mozes import pipeline_v2c as pipeline
from mozes.publication_control import dispatch_decision, verify_receipt

NOW = datetime(2026, 10, 12, 6, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def checkout_scripts_namespace(monkeypatch):
    # scripts/ is checkout-only, not part of the installed mozes package.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))



def test_failed_request_cannot_hide_aging_unchanged_checkpoint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, 'DB_PATH', tmp_path/'monitor.db')
    conn = pipeline.db.connect(pipeline.DB_PATH)
    pipeline.ensure_schema(conn)
    payload = {'live': [], 'summary': {}, 'build': {'change_ledger': 'a'*64}}
    fingerprint = pipeline.semantic_fingerprint(payload)
    pipeline.state_put(conn, 'publish_request', {'fingerprint':fingerprint,'at':(NOW-timedelta(minutes=5)).isoformat()})
    conn.close()
    Path('.monitor').mkdir(exist_ok=True)
    Path('.monitor/published-ledger.json').write_text(json.dumps({
        'schema':2, 'change_ledger':'a'*64, 'fingerprint':fingerprint,
        'checkpoint':{'checkpoint_at':(NOW-timedelta(minutes=60)).isoformat()}}))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return NOW
    monkeypatch.setattr(pipeline, 'datetime', Clock)
    monkeypatch.setattr(pipeline, 'run_priority_pipeline', lambda conn: {'status':'OK'})
    monkeypatch.setattr(pipeline, 'export_payload', lambda *args, **kwargs: payload)
    monkeypatch.setattr(pipeline, 'trim_cache', lambda *args: None)
    assert pipeline.main(['monitor','--priority-only']) == 0
    assert json.loads(Path('.monitor/manifest.json').read_text())['should_publish'] is True


@pytest.mark.parametrize('age,due', [(0,False),(24,False),(25,True),(45,True),(60,True)])
def test_unchanged_checkpoint_cadence_uses_success_only(tmp_path, age, due):
    payload={'live':[],'build':{'change_ledger':'a'*64}}
    receipt=tmp_path/'receipt.json'
    receipt.write_text(json.dumps({'schema':2,'change_ledger':'a'*64,
        'fingerprint':pipeline.semantic_fingerprint(payload),
        'checkpoint':{'checkpoint_at':(NOW-timedelta(minutes=age)).isoformat()}}))
    assert pipeline.publication_due(payload, now=NOW, receipt_path=receipt) is due


@pytest.mark.parametrize('receipt', [{}, {'change_ledger':'a'*64},
    {'schema':2,'checkpoint':{'checkpoint_at':None}},
    {'schema':2,'checkpoint':{'checkpoint_at':'2026-10-13T00:00:00+00:00'}}])
def test_missing_legacy_or_invalid_success_never_acknowledges_freshness(tmp_path, receipt):
    path=tmp_path/'receipt.json'; path.write_text(json.dumps(receipt))
    assert pipeline.publication_due({'build':{'change_ledger':'a'*64}},now=NOW,receipt_path=path)


@pytest.mark.parametrize('status', ['queued','in_progress','waiting','pending','requested'])
def test_live_pages_run_suppresses_duplicate_dispatch_even_when_old(status):
    assert dispatch_decision([{'status':status,'created_at':(NOW-timedelta(hours=1)).isoformat()}],now=NOW)=='publish_running'


def test_skipped_ci_pages_run_does_not_throttle_a_real_publication_request():
    rows=[{'status':'completed','conclusion':'failure','created_at':(NOW-timedelta(minutes=5)).isoformat()},
          {'status':'completed','conclusion':'skipped','created_at':(NOW-timedelta(seconds=10)).isoformat()}]
    assert dispatch_decision(rows,now=NOW)=='publish_requested_new'


@pytest.mark.parametrize('conclusion', ['failure','cancelled','success'])
def test_terminal_attempt_throttle_is_two_minutes_never_one_hour(conclusion):
    def decision(age):
        return dispatch_decision([{'status':'completed','conclusion':conclusion,
            'created_at':(NOW-timedelta(seconds=age)).isoformat()}],now=NOW)
    assert decision(119)=='publish_requested'
    assert decision(120)=='publish_requested_new'
    assert decision(300)=='publish_requested_new'


def verified():
    run={'id':10,'status':'completed','conclusion':'success','run_attempt':1,'head_branch':'main',
         'head_repository':{'full_name':'Owner/Repo'},'path':'.github/workflows/pages.yml',
         'event':'workflow_dispatch','head_sha':'b'*40,'created_at':(NOW-timedelta(minutes=5)).isoformat(),
         'updated_at':NOW.isoformat()}
    value={'schema':2,'pages_run_id':'10','code_sha':'b'*40,'change_ledger':'a'*64,'fingerprint':'c'*64,
           'checkpoint':{'schema':1,'consumer_only':True,'source_run_id':'9','artifact_id':8,
                         'db_sha256':'d'*64,'checkpoint_at':(NOW-timedelta(minutes=6)).isoformat()}}
    jobs={'total_count':1,'jobs':[{'name':'deploy','conclusion':'success','started_at':(NOW-timedelta(minutes=4)).isoformat(),
           'steps':[{'name':name,'status':'completed','conclusion':'success'} for name in
                    ('Deploy GitHub Pages','Acknowledge ledger only after successful deployment')]}]}
    artifacts={'total_count':1,'artifacts':[{'name':'mozes-published-ledger','expired':False,'size_in_bytes':100,
               'workflow_run':{'id':10},'created_at':(NOW-timedelta(minutes=1)).isoformat()}]}
    return run,value,jobs,artifacts


@pytest.mark.parametrize('problem', ['none','owner','failed','cancelled','rerun','identity','future','deployment','expired','duplicate','incomplete'])
def test_success_ack_requires_identity_order_and_verified_deployment(problem):
    run,value,jobs,artifacts=verified()
    if problem=='owner':run['head_branch']='other'
    if problem in ('failed','cancelled'):run['conclusion']=problem
    if problem=='rerun':run['run_attempt']=2
    if problem=='identity':value['pages_run_id']='11'
    if problem=='future':value['checkpoint']['checkpoint_at']=(NOW+timedelta(hours=1)).isoformat()
    if problem=='deployment':jobs['jobs'][0]['steps'][0]['conclusion']='failure'
    if problem=='expired':artifacts['artifacts'][0]['expired']=True
    if problem=='duplicate':artifacts['artifacts']*=2;artifacts['total_count']=2
    if problem=='incomplete':artifacts['total_count']=2
    def gh(*args): return json.dumps(jobs if '/jobs?' in args[1] else artifacts)
    if problem=='none':verify_receipt('Owner/Repo',run,value,gh)
    else:
        with pytest.raises(RuntimeError):verify_receipt('Owner/Repo',run,value,gh)


def test_pages_serializes_deploy_and_checks_snapshot_before_publish():
    text=Path('.github/workflows/pages.yml').read_text()
    assert 'group: mozes-hot-monitor' in text and 'cancel-in-progress: false' in text
    assert text.index('publication_control preflight') < text.index('uses: actions/deploy-pages@v4')
    assert 'timeout-minutes: 8' in text

@pytest.mark.parametrize('problem', ['none','new_artifact','older_checkpoint','unknown_success','superseded_code'])
def test_predeploy_movement_and_no_downgrade(tmp_path, monkeypatch, problem):
    from mozes import publication_control as control
    from scripts import restore_pages_state, restore_monitor_state
    run,value,_,_=verified()
    path=tmp_path/'publish-receipt.json';path.write_text(json.dumps(value))
    monkeypatch.chdir(tmp_path)
    Path('.monitor').mkdir()
    previous={**value,'checkpoint':{**value['checkpoint']}}
    if problem=='older_checkpoint':previous['checkpoint']['checkpoint_at']=NOW.isoformat()
    Path('.monitor/published-ledger.json').write_text(json.dumps(previous))
    monkeypatch.setattr(restore_pages_state,'newest',lambda:({'id':9},{'id':99 if problem=='new_artifact' else 8}))
    monkeypatch.setattr(restore_pages_state,'gh',lambda *args,**kwargs:json.dumps({'object':{'sha':'f'*40 if problem=='superseded_code' else 'b'*40}}))
    monkeypatch.setattr(restore_monitor_state,'restore_published_receipt',lambda repo,**kwargs:None if problem=='unknown_success' else previous)
    if problem=='none':control.preflight(path)
    else:
        with pytest.raises(RuntimeError):control.preflight(path)
    assert restore_pages_state.RESTORE_DEADLINE is None


def test_full_verified_receipt_roundtrip_preserves_checkpoint(tmp_path, monkeypatch):
    from scripts import restore_monitor_state as restore
    run,value,jobs,artifacts=verified()
    run['name']='deploy-pages'
    monkeypatch.chdir(tmp_path)
    def gh(*args, **kwargs):
        if args[0]=='api' and '/workflows/' in args[1]: return json.dumps({'total_count':1,'workflow_runs':[run]})
        if args[0]=='api':
            if '/jobs?' in args[1]:return json.dumps(jobs)
            if '/artifacts?' in args[1]:return json.dumps(artifacts)
            return json.dumps(run)
        Path(args[-1],'publish-receipt.json').write_text(json.dumps(value))
        return ''
    monkeypatch.setattr(restore,'gh',gh)
    assert restore.restore_published_receipt('Owner/Repo')==value
    assert json.loads(Path('.monitor/published-ledger.json').read_text())==value
    # A skipped/failed deploy never replaces the receipt with an acknowledgement.
    jobs['jobs'][0]['steps'][0]['conclusion']='failure'
    assert restore.restore_published_receipt('Owner/Repo') is None
    assert not Path('.monitor/published-ledger.json').exists()


def test_stale_active_pages_run_is_treated_as_dead_and_redispatched():
    rows=[{'id':1,'status':'in_progress','created_at':(NOW-timedelta(hours=2)).isoformat(),
           'updated_at':(NOW-timedelta(minutes=90)).isoformat()}]
    assert dispatch_decision(rows,now=NOW)=='publish_requested_new'


def test_fresh_active_pages_run_still_blocks_dispatch():
    rows=[{'id':1,'status':'in_progress','created_at':(NOW-timedelta(minutes=10)).isoformat(),
           'updated_at':(NOW-timedelta(minutes=3)).isoformat()}]
    assert dispatch_decision(rows,now=NOW)=='publish_running'


def test_active_run_without_updated_at_is_never_overridden():
    rows=[{'id':1,'status':'queued','created_at':(NOW-timedelta(hours=3)).isoformat()}]
    assert dispatch_decision(rows,now=NOW)=='publish_running'
