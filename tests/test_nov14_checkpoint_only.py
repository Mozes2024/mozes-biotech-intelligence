import copy,json,sqlite3,shutil
from datetime import datetime,timezone
from pathlib import Path
import pytest
from mozes import nov14_checkpoint_only as n

def unknown(rid=43096405392):
 a,b=n.UNKNOWN[rid]
 return {'id':rid,'head_sha':n.OLD_HEAD,'status':'completed','conclusion':'startup_failure','run_attempt':1,'created_at':a,'updated_at':b,'head_branch':'main','event':'workflow_dispatch','name':'lightweight-live-monitor','head_repository':{'full_name':n.REPO}}
def test_unknown_is_never_no_write():
 r=n.unknown_decision(unknown(),{'total_count':0,'jobs':[]});assert r['decision']=='EXPLICIT_UNCERTAINTY_ACCEPTED_NOT_NO_WRITE'
@pytest.mark.parametrize('key,value',[('head_sha','a'*40),('status','queued'),('conclusion','failure'),('run_attempt',2),('created_at','2026-11-11T20:30:00Z'),('updated_at','2026-11-11T21:00:00Z'),('head_branch','other'),('event','schedule'),('name','other')])
def test_unknown_metadata_drift_rejected(key,value):
 r=unknown();r[key]=value
 with pytest.raises(RuntimeError):n.unknown_decision(r,{'total_count':0,'jobs':[]})
@pytest.mark.parametrize('jobs',[{'total_count':1,'jobs':[{}]},{'jobs':[]},{'total_count':0,'jobs':[{}]}])
def test_unknown_jobs_drift(jobs):
 with pytest.raises(RuntimeError):n.unknown_decision(unknown(),jobs)
def test_unlisted_unknown():
 r=unknown();r['id']=999
 with pytest.raises(RuntimeError):n.unknown_decision(r,{'total_count':0,'jobs':[]})
def test_independent_incident_ledger_no_hidden_filter(monkeypatch):
 calls=[]
 def validate(repo,since,runner):
  r=json.loads(runner('api','/workflows/x'))['workflow_runs'][0];j=json.loads(runner('api','/jobs?'));calls.append(r['id']);assert j=={'jobs':[],'total_count':0}
 monkeypatch.setattr(n,'audit_producers',validate)
 rows=[unknown(i) for i in n.UNKNOWN]+[{**unknown(),'id':i,'conclusion':'failure'} for i in range(1,557)]
 proof=n.reconcile(rows,lambda i:{'total_count':0,'jobs':[]},'self')
 assert proof['relevant_count']==558 and len(calls)==556 and proof['global_500_policy_changed'] is False
 assert sum(r['decision']=='EXPLICIT_UNCERTAINTY_ACCEPTED_NOT_NO_WRITE' for r in proof['per_run'])==2
 assert {r['run_id'] for r in proof['per_run']}=={str(r['id']) for r in rows}
def test_new_ambiguous_run_rejected():
 rows=[unknown(i) for i in n.UNKNOWN]+[{**unknown(),'id':999}]
 with pytest.raises(RuntimeError,match='incomplete or unexpected'):n.reconcile(rows,lambda i:{'total_count':0,'jobs':[]},'self')
def test_incident_resource_limit_is_finite():
 rows=[unknown(i) for i in n.UNKNOWN]+[{**unknown(),'id':i} for i in range(1,1001)]
 with pytest.raises(RuntimeError,match='resource limit'):n.reconcile(rows,lambda i:None,'self')
def test_missing_unknown_records_fails():
 with pytest.raises(RuntimeError,match='missing'):n.reconcile([],lambda i:None,'self')
def test_stage_bad_hash_no_output(tmp_path):
 src=tmp_path/'source';src.mkdir();(src/'mozes-live.db').write_bytes(b'wrong')
 with pytest.raises(RuntimeError,match='hash'):n.stage(src,tmp_path/'out','123',{},datetime.now(timezone.utc))
 assert not (tmp_path/'out').exists()
def test_provider_rejects_write_or_incomplete(tmp_path):
 for evidence in [[],[{}]*3,[{'success':True,'meta':{'rows_written':1}}]*3]:
  with pytest.raises(RuntimeError):n.verify_provider(tmp_path/'absent',evidence)
def test_context_requires_disabled_by_default(monkeypatch):
 monkeypatch.delenv('MOZES_NOV14_APPROVED_SHA',raising=False)
 with pytest.raises(RuntimeError,match='SHA missing'):n.context()
def test_claim_never_retries_on_error(monkeypatch):
 monkeypatch.setattr(n,'context',lambda:'a'*40);calls=[]
 def fail(*a,**kw):calls.append(a);raise RuntimeError('ambiguous')
 monkeypatch.setattr(n.subprocess,'check_output',fail)
 with pytest.raises(RuntimeError,match='ambiguous'):n.claim()
 assert len(calls)==1
 assert '--method' in calls[0][0] and 'POST' in calls[0][0] and 'git/refs' in calls[0][0][2]
def test_claim_does_not_adopt_existing_ref(monkeypatch):
 monkeypatch.setattr(n,'context',lambda:'a'*40);calls=[]
 def conflict(*a,**kw):calls.append(a);raise n.subprocess.CalledProcessError(1,a)
 monkeypatch.setattr(n.subprocess,'check_output',conflict)
 with pytest.raises(n.subprocess.CalledProcessError):n.claim()
 assert len(calls)==1
@pytest.mark.parametrize('status',['200','202','302'])
def test_claim_requires_actual_creation_201(monkeypatch,status):
 monkeypatch.setattr(n,'context',lambda:'a'*40)
 monkeypatch.setattr(n.subprocess,'check_output',lambda *a,**kw:'HTTP/2.0 '+status+' Result\nContent-Type: application/json\n\n'+json.dumps({'ref':'refs/'+n.LATCH,'object':{'sha':'a'*40}}))
 with pytest.raises(RuntimeError,match='confirmed201'):n.claim()
def test_claim_confirmed_201(monkeypatch):
 monkeypatch.setattr(n,'context',lambda:'a'*40)
 monkeypatch.setattr(n.subprocess,'check_output',lambda *a,**kw:'HTTP/2.0 201 Created\nContent-Type: application/json\n\n'+json.dumps({'ref':'refs/'+n.LATCH,'object':{'sha':'a'*40}}))
 n.claim()
def test_claim_conflicting_ref_rejected(monkeypatch):
 monkeypatch.setattr(n,'context',lambda:'a'*40)
 monkeypatch.setattr(n.subprocess,'check_output',lambda *a,**kw:'HTTP/2.0 201 Created\n\n'+json.dumps({'ref':'refs/other','object':{'sha':'a'*40}}))
 with pytest.raises(RuntimeError,match='claim ambiguous'):n.claim()
def test_workflow_exact_checkpoint_only_boundaries():
 text=(Path(__file__).parents[1]/'.github/workflows/lightweight-monitor.yml').read_text()
 incident=text.split('  nov14_checkpoint_only:',1)[1].split('  incident_eligibility:',1)[0]
 assert "github.event_name == 'workflow_dispatch'" in incident and "github.ref == 'refs/heads/main'" in incident
 assert 'vars.MOZES_NOV14_APPROVED_SHA == github.sha' in incident and "inputs.nov14_checkpoint_only == 'checkpoint-only:43093359153:two-unknown-runs'" in incident
 assert incident.index('checkpoint_only claim')<incident.index('checkpoint_only prepare')<incident.index('actions/upload-artifact@')<incident.index('checkpoint_only verify_upload')
 assert 'continue-on-error' not in incident and 'always()' not in incident
 assert 'contents: write' in incident and 'actions: read' in incident
 assert all(k not in incident for k in ['EDGE_SYNC_TOKEN','MOZES_ALERT_FEED_TOKEN','MOZES_NTFY_URL','MOZES_WEBHOOK_URL','MOZES_SMTP_PASSWORD','mozes.publication_control','mozes.hot_monitor','edge_enrichment ack'])
 assert 'CLOUDFLARE_RECOVERY_READ_TOKEN' in incident
 for job,nextjob in [('incident_eligibility','prospective_claim'),('prospective_claim','monitor')]:
  part=text.split('  '+job+':',1)[1].split('  '+nextjob+':',1)[0]
  assert 'false && (' in part
 assert 'false && (' in text.split('  prospective_cleanup:',1)[1]
@pytest.mark.parametrize('conclusion,steps,allowed',[('skipped',[],True),('success',[],False),('failure',[],False),('skipped',[{'name':'claim','conclusion':'skipped'}],False)])
def test_incident_job_only_ignorable_when_skipped_without_steps(conclusion,steps,allowed):
 from mozes.monitor_recovery import audit_producers,DELIVERY_STEPS
 run={**unknown(),'id':123,'conclusion':'failure'}
 job={'name':'monitor','conclusion':'failure','steps':[{'name':name,'conclusion':'skipped'} for name in sorted(DELIVERY_STEPS)]+[{'name':'Enrich the correlated Edge event','conclusion':'skipped'}]}
 jobs={'total_count':2,'jobs':[job,{'name':'nov14-checkpoint-only','conclusion':conclusion,'steps':steps}]}
 def capture(*args,**kw):return json.dumps({'workflow_runs':[run]}) if '/workflows/' in args[1] else json.dumps(jobs)
 if allowed:audit_producers(n.REPO,datetime.fromisoformat(n.STAMP),capture)
 else:
  with pytest.raises(RuntimeError):audit_producers(n.REPO,datetime.fromisoformat(n.STAMP),capture)

def test_pages_need_separate_restart_sha_not_checkpoint_approval():
 text=(Path(__file__).parents[1]/'.github/workflows/pages.yml').read_text()
 assert 'vars.MOZES_NOV14_RESTART_SHA' in text
 assert 'MOZES_NOV14_APPROVED_SHA' not in text
 assert "MOZES_NOV14_RESTART_MODE == 'trial' && github.event_name == 'workflow_dispatch'" in text
 assert "needs.build.outputs.publish == 'true'" in text

def test_no_variables_rest_permission_or_new_token_dependency():
 source=Path(n.__file__).read_text()
 assert 'actions/variables' not in source
 text=(Path(__file__).parents[1]/'.github/workflows/lightweight-monitor.yml').read_text()
 incident=text.split('  nov14_checkpoint_only:',1)[1].split('  incident_eligibility:',1)[0]
 assert 'MOZES_NOV14_CODE_HOLD: checkpoint-only-20261114' in incident
 assert 'MOZES_NOV14_RECOVERY_HOLD' not in text
 assert 'CLOUDFLARE_API_TOKEN' not in incident

def test_old_incident_pilot_remain_paused_and_restart_is_separate():
 root=Path(__file__).parents[1]
 text=(root/'.github/workflows/lightweight-monitor.yml').read_text()
 for job in ['incident_eligibility','prospective_claim','prospective_cleanup']:
  part=text.split('  '+job+':',1)[1]
  expr=part.split('    if: >-\n',1)[1].split('\n    ',1)[0]
  assert expr.strip()=='false && ('
 pages=(root/'.github/workflows/pages.yml').read_text()
 assert 'vars.MOZES_NOV14_RESTART_SHA' in pages
 assert 'MOZES_NOV14_APPROVED_SHA' not in pages
 assert "    if: needs.build.outputs.publish == 'true'" in pages
 monitor=text.split('  monitor:',1)[1].split('  prospective_cleanup:',1)[0]
 assert 'vars.MOZES_NOV14_RESTART_SHA == github.sha' in monitor
 assert 'MOZES_NOV14_APPROVED_SHA' not in monitor
