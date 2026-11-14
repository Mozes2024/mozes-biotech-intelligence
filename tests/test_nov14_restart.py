from pathlib import Path
import hashlib,json
import pytest
from mozes import nov14_restart as n

@pytest.fixture(autouse=True)
def repository_scripts_import_path(monkeypatch):
 # The pytest console entry point does not add cwd to sys.path. Runtime uses
 # python -m from the checkout root. Match that path only for each test.
 monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))

def fixture(tmp_path,monkeypatch):
 files={'mozes-live.db':b'unchanged database','manifest.json':b'{"data_publishable":false}',
 'edge-checkpoint.json':b'{"producer_run_id":"43622484709"}',
 'recovery-proof.json':json.dumps({'checkpoint_only':True,'collection_resumed':False,'source_observation_freshness_advanced':False,'uncertainty_run_ids':['43096405392','43098081032']}).encode()}
 for name,data in files.items():(tmp_path/name).write_bytes(data)
 for name,key in [('mozes-live.db','DB_SHA'),('manifest.json','MANIFEST_SHA'),('edge-checkpoint.json','CP_SHA'),('recovery-proof.json','PROOF_SHA')]:monkeypatch.setattr(n,key,hashlib.sha256(files[name]).hexdigest())
 return tmp_path/'mozes-live.db'

def test_initial_exact(tmp_path,monkeypatch):
 source=fixture(tmp_path,monkeypatch);assert n.historical_proof(source,n.SOURCE)==source.with_name('recovery-proof.json')

@pytest.mark.parametrize('name',['mozes-live.db','manifest.json','edge-checkpoint.json','recovery-proof.json'])
def test_initial_drift(tmp_path,monkeypatch,name):
 source=fixture(tmp_path,monkeypatch);(tmp_path/name).write_bytes(b'changed')
 with pytest.raises((ValueError,json.JSONDecodeError)):n.historical_proof(source,n.SOURCE)

def test_initial_missing_ledger(tmp_path,monkeypatch):
 source=fixture(tmp_path,monkeypatch);source.with_name('recovery-proof.json').unlink()
 with pytest.raises(ValueError,match='ledger missing'):n.historical_proof(source,n.SOURCE)

def test_descendant_retains_exact_history_not_current_db_hash(tmp_path,monkeypatch):
 source=fixture(tmp_path,monkeypatch);source.write_bytes(b'new genuine observations')
 assert n.historical_proof(source,'future-id')==source.with_name('recovery-proof.json')

def test_descendant_changed_history_rejected(tmp_path,monkeypatch):
 source=fixture(tmp_path,monkeypatch);p=source.with_name('recovery-proof.json');x=json.loads(p.read_text());x['uncertainty_run_ids']=[];p.write_text(json.dumps(x))
 with pytest.raises(ValueError,match='ledger changed'):n.historical_proof(source,'future-id')

def test_legacy_other_recovery_unchanged(tmp_path):
 source=tmp_path/'mozes-live.db';source.write_bytes(b'legacy');assert n.historical_proof(source,'other') is None
 source.with_name('recovery-proof.json').write_text('{"legacy":true}');assert n.historical_proof(source,'other') is None

@pytest.fixture
def env(monkeypatch):
 import os
 for key in list(os.environ):
  if key.startswith(('GITHUB_','MOZES_NOV14_','NOV14_')):monkeypatch.delenv(key)
 sha='a'*40
 for k,v in {'GITHUB_REPOSITORY':n.REPO,'GITHUB_REF':'refs/heads/main','GITHUB_SHA':sha,'GITHUB_RUN_ATTEMPT':'1','GITHUB_WORKFLOW':'lightweight-live-monitor','GITHUB_JOB':'monitor','GITHUB_EVENT_NAME':'workflow_dispatch','MOZES_NOV14_RESTART_SHA':sha,'MOZES_NOV14_RESTART_MODE':'trial','NOV14_RESTART_INPUT':'one-ordinary-pass'}.items():monkeypatch.setenv(k,v)
 def api(path):
  if path=='commits/main':return {'sha':sha}
  return {'workflow_runs':[{'head_sha':sha,'head_branch':'main','event':'push','status':'completed','conclusion':'success'}]}
 monkeypatch.setattr(n,'api',api)
 return sha

def test_trial_context(env):assert n.context()=='trial'

@pytest.mark.parametrize('key,value',[('GITHUB_REPOSITORY','other/repo'),('GITHUB_REF','refs/heads/other'),('GITHUB_SHA','b'*40),('GITHUB_RUN_ATTEMPT','2'),('GITHUB_WORKFLOW','other'),('GITHUB_JOB','other'),('GITHUB_EVENT_NAME','schedule'),('MOZES_NOV14_RESTART_MODE',''),('NOV14_RESTART_INPUT','')])
def test_wrong_trial_context(env,monkeypatch,key,value):
 monkeypatch.setenv(key,value)
 with pytest.raises(ValueError):n.context()

def test_continuous_schedule(env,monkeypatch):
 monkeypatch.setenv('MOZES_NOV14_RESTART_MODE','continuous');monkeypatch.setenv('GITHUB_EVENT_NAME','schedule');monkeypatch.delenv('NOV14_RESTART_INPUT');assert n.context()=='continuous'

def test_main_drift(env,monkeypatch):
 monkeypatch.setattr(n,'api',lambda path:{'sha':'b'*40})
 with pytest.raises(ValueError,match='main changed'):n.context()

def test_main_ci_failure(env,monkeypatch):
 monkeypatch.setattr(n,'api',lambda path: {'sha':env} if path=='commits/main' else {'workflow_runs':[]})
 with pytest.raises(ValueError,match='CI not green'):n.context()

def test_trial_source_superseded(env,monkeypatch):
 from scripts import restore_monitor_state as r
 monkeypatch.setattr(r,'_list_success_runs',lambda:[{'databaseId':'newer'}])
 with pytest.raises(ValueError,match='superseded'):n.preflight()

def test_trial_fixed_artifact(env,monkeypatch):
 from scripts import restore_monitor_state as r
 monkeypatch.setattr(r,'_list_success_runs',lambda:[{'databaseId':n.SOURCE}]);monkeypatch.setattr(n,'context',lambda:'trial')
 monkeypatch.setattr(n,'api',lambda path:{'expired':False,'digest':'sha256:'+n.ZIP_SHA,'workflow_run':{'id':int(n.SOURCE)}});n.preflight()

@pytest.mark.parametrize('name,value',[('expired',True),('digest','sha256:wrong'),('workflow_run',{'id':1})])
def test_trial_artifact_drift(env,monkeypatch,name,value):
 from scripts import restore_monitor_state as r
 monkeypatch.setattr(r,'_list_success_runs',lambda:[{'databaseId':n.SOURCE}]);monkeypatch.setattr(n,'context',lambda:'trial')
 artifact={'expired':False,'digest':'sha256:'+n.ZIP_SHA,'workflow_run':{'id':int(n.SOURCE)}};artifact[name]=value
 monkeypatch.setattr(n,'api',lambda path:artifact)
 with pytest.raises(ValueError,match='unavailable or changed'):n.preflight()

def test_continuous_no_trial_provider_probe(monkeypatch):
 monkeypatch.setattr(n,'context',lambda:'continuous');monkeypatch.setattr(n,'api',lambda path:(_ for _ in ()).throw(AssertionError('extra API call')));n.preflight();n.before_write()

def test_restart_workflow_gates():
 p=Path(__file__).parents[1];s=(p/'.github/workflows/lightweight-monitor.yml').read_text();monitor=s.split('  monitor:\n',1)[1].split('  prospective_cleanup:',1)[0]
 assert "vars.MOZES_NOV14_RESTART_SHA == github.sha" in monitor
 assert "vars.MOZES_NOV14_RESTART_MODE == 'trial'" in monitor and "inputs.nov14_restart == 'one-ordinary-pass'" in monitor
 assert monitor.index('nov14_restart preflight') < monitor.index('Restore the single monitor-state lineage') < monitor.index('nov14_restart before_write') < monitor.index('Synchronize verified issuer universe')
 assert "inputs.source_run_id == ''" in monitor and "inputs.recovery_checkpoint_sha256 == ''" in monitor
 assert s.count('false && (')==3 # Old incident/pilot/cleanup remain paused.
 pages=(p/'.github/workflows/pages.yml').read_text();assert "vars.MOZES_NOV14_RESTART_MODE == 'trial' && github.event_name == 'workflow_dispatch'" in pages

def test_before_write_full_completion_audit_and_provider_check(tmp_path,monkeypatch):
 from scripts import restore_monitor_state as r
 from mozes import monitor_recovery as mr,nov14_checkpoint_only as checkpoint
 source=fixture(tmp_path,monkeypatch);monkeypatch.setenv('MOZES_DB_PATH',str(source));monkeypatch.setattr(n,'context',lambda:'trial')
 calls=[]
 def audit(repo,since,gh,**kw):
  calls.append(('audit',since.isoformat(),kw));assert kw=={'full_history':True,'source_run_id':n.SOURCE}
 monkeypatch.setattr(mr,'audit_producers',audit);monkeypatch.setattr(r,'_list_success_runs',lambda:[{'databaseId':n.SOURCE}])
 monkeypatch.setattr(checkpoint,'read_provider',lambda:['fixed read response'])
 monkeypatch.setattr(checkpoint,'verify_provider',lambda db,x:calls.append(('provider',db,x)))
 n.before_write();assert [x[0]for x in calls]==['audit','provider'];assert calls[0][1]=='2026-11-14T17:44:49.340200+00:00'

def test_before_write_source_changed_stops_before_provider(tmp_path,monkeypatch):
 from scripts import restore_monitor_state as r
 from mozes import monitor_recovery as mr,nov14_checkpoint_only as checkpoint
 source=fixture(tmp_path,monkeypatch);monkeypatch.setenv('MOZES_DB_PATH',str(source));monkeypatch.setattr(n,'context',lambda:'trial')
 monkeypatch.setattr(mr,'audit_producers',lambda *a,**k:None);monkeypatch.setattr(r,'_list_success_runs',lambda:[{'databaseId':'newer'}])
 monkeypatch.setattr(checkpoint,'read_provider',lambda:(_ for _ in ()).throw(AssertionError('provider must not run')))
 with pytest.raises(ValueError,match='source changed'):n.before_write()

def test_before_write_provider_refusal_stops(tmp_path,monkeypatch):
 from scripts import restore_monitor_state as r
 from mozes import monitor_recovery as mr,nov14_checkpoint_only as checkpoint
 source=fixture(tmp_path,monkeypatch);monkeypatch.setenv('MOZES_DB_PATH',str(source));monkeypatch.setattr(n,'context',lambda:'trial')
 monkeypatch.setattr(mr,'audit_producers',lambda *a,**k:None);monkeypatch.setattr(r,'_list_success_runs',lambda:[{'databaseId':n.SOURCE}]);monkeypatch.setattr(checkpoint,'read_provider',lambda:[])
 monkeypatch.setattr(checkpoint,'verify_provider',lambda *a:(_ for _ in ()).throw(RuntimeError('provider drift')))
 with pytest.raises(RuntimeError,match='provider drift'):n.before_write()
