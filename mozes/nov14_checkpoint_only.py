"""November 14 checkpoint-only incident candidate. Disabled by workflow/hold gates.

This module does not establish owner permission. Its uncertainty acceptance and
source/cap route are changes requiring the exact reviewed activation approval.
No publisher fetch, outbox dispatch, ACK, public export or ordinary restore.
"""
from __future__ import annotations
import argparse,hashlib,json,os,re,shutil,sqlite3,subprocess,tempfile,time,urllib.request,zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from mozes.monitor_recovery import _complete_producer_inventory,_inventory_signature,audit_producers
REPO='Mozes2024/mozes-biotech-intelligence'
SOURCE='43093359153';ARTIFACT=13889173652
ZIP_SHA='2dabf8604bd74b2a51b2b2913f422f08a2d5b43fb400681780821ed491fc5d25'
DB_SHA='afc818a17055937baff4d1263abb53a1688978a439d74b2d1164b8d3d7b1f2b2'
STAMP='2026-11-11T20:09:07+00:00'
OLD_HEAD='e63f0c33345f3b3d58d51194f05f4abe684b8618'
LATCH='tags/bio-20261114-checkpoint-only-43093359153'
ACCOUNT='75b91ca20508a72979cb5337ce1cc7e2';DATABASE='9baf99f2-1ccc-4415-affd-2708a7210e7b'
QUERY="SELECT event_id,enrichment_ack_at,enrichment_change_id,enrichment_github_run_id FROM edge_events WHERE enrichment_ack_at IS NOT NULL ORDER BY event_id LIMIT 1001; SELECT payload FROM alert_feed WHERE id=1; SELECT MAX(completed_at) AS latest_completion FROM edge_candidates;"
UNKNOWN={43096405392:('2026-11-11T20:29:59Z','2026-11-11T20:37:28Z'),43098081032:('2026-11-11T20:47:07Z','2026-11-11T20:52:41Z')}
INCIDENT_MAX_RELEVANT=1000 # Explicitly reviewed incident-only resource limit, not global policy.
def require(ok,why):
 if not ok:raise RuntimeError('Nov14 incident: '+why)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def encode(x):return json.dumps(x,sort_keys=True,separators=(',',':'))
DEADLINE=None
def gh(*args,timeout=30):
 remaining=(DEADLINE-time.monotonic()) if DEADLINE else timeout
 require(remaining>0,'preflight deadline exceeded')
 return subprocess.check_output(['gh',*args],text=True,timeout=min(timeout,remaining))
def api(path):return json.loads(gh('api',f'repos/{REPO}/{path}'))
def context():
 approved=os.environ.get('MOZES_NOV14_APPROVED_SHA','')
 require(re.fullmatch('[a-f0-9]{40}',approved) is not None,'reviewed SHA missing')
 require(os.environ.get('GITHUB_REPOSITORY')==REPO and os.environ.get('GITHUB_REF')=='refs/heads/main' and os.environ.get('GITHUB_SHA')==approved and os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and os.environ.get('GITHUB_RUN_ATTEMPT')=='1','execution identity')
 require(os.environ.get('MOZES_NOV14_CODE_HOLD')=='checkpoint-only-20261114','code-pinned collection hold absent')
 require(os.environ.get('GITHUB_WORKFLOW')=='lightweight-live-monitor' and os.environ.get('GITHUB_JOB')=='nov14_checkpoint_only' and os.environ.get('GITHUB_ACTIONS')=='true','workflow/job identity')
 require(os.environ.get('NOV14_ACCEPTANCE')=='checkpoint-only:43093359153:two-unknown-runs','exact phase not selected')
 require(api('commits/main')['sha']==approved,'main drift')
 ci=api('actions/workflows/ci.yml/runs?head_sha='+approved+'&per_page=5')['workflow_runs']
 require(any(r.get('head_sha')==approved and r.get('head_branch')=='main' and r.get('event')=='push' and r.get('status')=='completed' and r.get('conclusion')=='success' for r in ci),'reviewed main CI not green')
 require(not any(os.environ.get(k) for k in ('EDGE_SYNC_TOKEN','MOZES_ALERT_FEED_TOKEN','MOZES_NTFY_URL','MOZES_WEBHOOK_URL','MOZES_SMTP_USER','MOZES_SMTP_PASSWORD')),'write/notification credential exposed')
 return approved

def unknown_decision(run,jobs):
 rid=run['id'];require(rid in UNKNOWN,'unlisted ambiguous producer')
 created,updated=UNKNOWN[rid]
 require(all((run.get(k)==v) for k,v in {'head_sha':OLD_HEAD,'status':'completed','conclusion':'startup_failure','run_attempt':1,'created_at':created,'updated_at':updated,'head_branch':'main','event':'workflow_dispatch','name':'lightweight-live-monitor'}.items()),'unknown metadata drift')
 require((run.get('head_repository') or {}).get('full_name')==REPO,'unknown origin drift')
 require(jobs.get('total_count')==0 and jobs.get('jobs')==[],'unknown job evidence changed')
 return {'run_id':str(rid),'decision':'EXPLICIT_UNCERTAINTY_ACCEPTED_NOT_NO_WRITE','metadata_sha256':hashlib.sha256(encode(run).encode()).hexdigest()}

def reconcile(runs,read_jobs,self_id):
 selected=[r for r in runs if str(r['id']) not in (SOURCE,str(self_id)) and (r['status']!='completed' or datetime.fromisoformat(r['updated_at'].replace('Z','+00:00'))>datetime.fromisoformat(STAMP))]
 require(len(selected)<=INCIDENT_MAX_RELEVANT,'incident resource limit; no partial audit')
 require(set(UNKNOWN).issubset({r['id'] for r in selected}),'unknown records missing from complete inventory')
 def check(run):
  jobs=read_jobs(run['id'])
  if run['id'] in UNKNOWN:return unknown_decision(run,jobs)
  def captured(*args,**kwargs):
   if '/workflows/' in args[1]:return encode({'workflow_runs':[run]})
   if '/jobs?' in args[1]:return encode(jobs)
   raise RuntimeError('unreviewed extra evidence required')
  audit_producers(REPO,datetime.fromisoformat(STAMP),captured)
  return {'run_id':str(run['id']),'decision':'EXISTING_NO_WRITE_VALIDATOR_PASS','metadata_sha256':hashlib.sha256(encode(run).encode()).hexdigest(),'jobs_sha256':hashlib.sha256(encode(jobs).encode()).hexdigest()}
 with ThreadPoolExecutor(max_workers=3) as pool:result=list(pool.map(check,selected))
 return {'relevant_count':len(selected),'per_run':result,'uncertainty_run_ids':[str(i) for i in sorted(UNKNOWN)],'global_500_policy_changed':False}

def verify_provider(source,evidence):
 require(isinstance(evidence,list) and len(evidence)==3,'D1 response incomplete')
 require(all(r.get('success') is True and r.get('meta',{}).get('rows_written')==0 for r in evidence),'D1 read proof invalid')
 acks=evidence[0]['results'];require(len(acks)<1001,'ACK set incomplete')
 c=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
 try:
  links=dict(c.execute('SELECT edge_event_id,change_id FROM edge_event_links'))
  require(len({r['event_id'] for r in acks})==len(acks),'duplicate ACK IDs')
  actual={r['event_id']:r['enrichment_change_id'] for r in acks}
  require(actual==links and len(links)==330,'completion mapping drift')
  require(all(r.get('enrichment_ack_at') and datetime.fromisoformat(r['enrichment_ack_at'].replace('Z','+00:00'))<=datetime.fromisoformat(STAMP) and str(r.get('enrichment_github_run_id')) not in {str(i) for i in UNKNOWN} for r in acks),'post-checkpoint or ambiguous completion')
  require(c.execute("SELECT COUNT(*) FROM alert_outbox WHERE status IN ('pending','sending','failed')").fetchone()[0]==0,'nonterminal delivery')
  feeds=evidence[1]['results'];require(len(feeds)==1,'current full feed missing')
  feed=json.loads(feeds[0]['payload']);require(feed.get('schema')==1 and isinstance(feed.get('alerts'),list),'feed shape')
  require(feed.get('generated_at')=='2026-11-11T20:09:14.000Z' and feed.get('sequence')==5916,'feed changed')
  require(feed.get('revision')=='923ddb35c0d055cccdc6c869bf62cae54196a97ec1484047d3be48c82fc3a50d','feed revision changed')
  require(len(feed['alerts'])==79,'feed row count changed')
  for a in feed['alerts']:
   require(c.execute('SELECT 1 FROM change_events WHERE change_id=?',(a['change_id'],)).fetchone() is not None,'published change absent')
   require(not a.get('delivered') or c.execute("SELECT 1 FROM alert_outbox WHERE change_id=? AND status='sent'",(a['change_id'],)).fetchone() is not None,'delivery receipt absent')
  completion=evidence[2]['results'];require(len(completion)==1 and completion[0]['latest_completion']=='2026-11-10T22:09:39.373Z','clinical completion drift')
  return {'edge_ack_count':330,'feed_alert_count':79,'full_feed_read':True,'historical_unknown_execution_proven_absent':False}
 finally:c.close()

def read_provider():
 token=os.environ.get('CLOUDFLARE_RECOVERY_READ_TOKEN','');require(bool(token),'existing D1 read credential unavailable')
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):return None
 req=urllib.request.Request(f'https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/d1/database/{DATABASE}/query',data=json.dumps({'sql':QUERY}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
 with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as response:raw=response.read(2097153)
 require(len(raw)<=2097152,'provider evidence oversized')
 x=json.loads(raw);require(x.get('success') is True and not x.get('errors'),'provider read rejected')
 return x['result']

def stage(source_dir,output,run_id,proof,now):
 source=source_dir/'mozes-live.db';require(digest(source)==DB_SHA,'source DB hash')
 cp=json.loads((source_dir/'edge-checkpoint.json').read_text());require(cp=={'schema':1,'producer_run_id':SOURCE,'created_at':STAMP,'db_sha256':DB_SHA},'original checkpoint identity')
 manifest=json.loads((source_dir/'manifest.json').read_text());require(manifest.get('generated_at')==STAMP and manifest.get('source_sha')==OLD_HEAD,'manifest drift')
 with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as c:require(c.execute('PRAGMA quick_check').fetchone()[0]=='ok','SQLite integrity')
 require(not output.exists(),'stage destination already exists')
 output.mkdir(parents=True);shutil.copy2(source,output/'mozes-live.db')
 manifest.update(should_publish=False,data_publishable=False,recovery_mode='CHECKPOINT_ONLY_COLLECTION_HELD',original_checkpoint_at=STAMP)
 (output/'manifest.json').write_text(encode(manifest))
 (output/'edge-checkpoint.json').write_text(encode({'schema':1,'producer_run_id':str(run_id),'created_at':now.isoformat(),'db_sha256':DB_SHA}))
 proof.update(source_run_id=SOURCE,source_db_sha256=DB_SHA,original_checkpoint_at=STAMP,checkpoint_only=True,collection_resumed=False,source_observation_freshness_advanced=False)
 (output/'recovery-proof.json').write_text(encode(proof))
 require(digest(output/'mozes-live.db')==DB_SHA and digest(source)==DB_SHA,'staged DB changed')
 return proof

def prepare():
 global DEADLINE
 start=time.monotonic();DEADLINE=start+480
 approved=context();self_id=os.environ['GITHUB_RUN_ID']
 latch=api('git/ref/'+LATCH);require(latch.get('object',{}).get('sha')==approved,'one-use latch missing or changed')
 # No restore invocation, source substitution, expired artifact or archive fallback.
 source_run=api('actions/runs/'+SOURCE)
 require(source_run.get('head_sha')==OLD_HEAD and source_run.get('conclusion')=='success' and source_run.get('run_attempt')==1 and source_run.get('head_branch')=='main' and source_run.get('name')=='lightweight-live-monitor' and (source_run.get('head_repository') or {}).get('full_name')==REPO,'source producer provenance')
 meta=api(f'actions/artifacts/{ARTIFACT}');require(meta.get('expired') is False and meta.get('digest')=='sha256:'+ZIP_SHA and str(meta.get('workflow_run',{}).get('id'))==SOURCE,'original artifact unavailable or changed')
 from scripts.restore_monitor_state import _list_success_runs
 require(str(_list_success_runs()[0]['databaseId'])==SOURCE,'newer checkpoint exists')
 before=_complete_producer_inventory(REPO,gh)
 require(all(r['status']=='completed' or str(r['id'])==self_id or r.get('head_sha')==approved for r in before),'old-revision queued/active producer must drain first')
 proof=reconcile(before,lambda rid:api(f'actions/runs/{rid}/jobs?filter=all&per_page=100'),self_id)
 with tempfile.TemporaryDirectory() as temp:
  temp=Path(temp);archive=temp/'source.zip'
  with archive.open('wb') as f:subprocess.run(['gh','api',f'repos/{REPO}/actions/artifacts/{ARTIFACT}/zip'],stdout=f,check=True,timeout=60)
  require(digest(archive)==ZIP_SHA,'ZIP digest')
  with zipfile.ZipFile(archive) as z:
   require(set(z.namelist())=={'mozes-live.db','manifest.json','edge-checkpoint.json'},'ZIP members differ')
   require(sum(i.file_size for i in z.infolist())<=450000000,'ZIP expanded size')
   z.extractall(temp/'source')
  proof['provider']=verify_provider(temp/'source/mozes-live.db',read_provider())
  after=_complete_producer_inventory(REPO,gh);require(_inventory_signature(before)==_inventory_signature(after),'producer inventory changed')
  require(str(_list_success_runs()[0]['databaseId'])==SOURCE,'newer checkpoint appeared')
  context() # Repeat immutable-code identity, SHA and green-CI checks before stage.
  refreshed=api(f'actions/artifacts/{ARTIFACT}');require(refreshed.get('expired') is False and refreshed.get('digest')=='sha256:'+ZIP_SHA,'source expired/changed during preflight')
  require(time.monotonic()-start<480,'8 minute preflight budget exceeded')
  proof['inventory_sha256']=hashlib.sha256(encode(sorted(after,key=lambda r:r['id'])).encode()).hexdigest()
  proof['approved_code_sha']=approved
  stage(temp/'source',Path('.incident-staged'),self_id,proof,datetime.now(timezone.utc))
 print(encode({'status':'STAGED_NOT_UPLOADED','db_sha256':DB_SHA,'relevant':proof['relevant_count'],'unknown_runs_explicitly_accepted':proof['uncertainty_run_ids']}))

def claim():
 approved=context()
 # create-ref is atomic and never adopts/retries an existing latch. No absence-based retry.
 body=encode({'ref':'refs/'+LATCH,'sha':approved})
 raw=subprocess.check_output(['gh','api',f'repos/{REPO}/git/refs','--include','--method','POST','--input','-'],input=body,text=True,timeout=30)
 header,sep,payload=raw.replace('\r\n','\n').partition('\n\n')
 require(bool(sep) and re.match(r'HTTP/[^ ]+ 201(?: |$)',header.splitlines()[0]) is not None,'claim response not confirmed201; no retry')
 result=json.loads(payload)
 require(result.get('ref')=='refs/'+LATCH and result.get('object',{}).get('sha')==approved,'claim ambiguous; permanent stop')
 print('ONE_USE_LATCH_CREATED_NO_RETRY')
def verify_upload():
 context();rid=os.environ['GITHUB_RUN_ID'];listing=api(f'actions/runs/{rid}/artifacts?per_page=100')
 require(listing.get('total_count')==len(listing.get('artifacts',[])),'upload inventory incomplete')
 rows=[r for r in listing['artifacts'] if r.get('name')=='mozes-live-monitor']
 require(len(rows)==1 and rows[0].get('expired') is False,'checkpoint upload missing or ambiguous')
 item=rows[0];require(str(item.get('workflow_run',{}).get('id'))==rid,'uploaded provenance')
 with tempfile.TemporaryDirectory() as t:
  p=Path(t)/'verify.zip'
  with p.open('wb') as f:subprocess.run(['gh','api',f"repos/{REPO}/actions/artifacts/{item['id']}/zip"],stdout=f,check=True,timeout=60)
  require(item.get('digest')=='sha256:'+digest(p),'uploaded ZIP digest')
  with zipfile.ZipFile(p) as z:
   require(set(z.namelist())=={'mozes-live.db','manifest.json','edge-checkpoint.json','recovery-proof.json'},'uploaded checkpoint layout')
   require(hashlib.sha256(z.read('mozes-live.db')).hexdigest()==DB_SHA,'uploaded DB digest')
   proof=json.loads(z.read('recovery-proof.json'));manifest=json.loads(z.read('manifest.json'));cp=json.loads(z.read('edge-checkpoint.json'))
   require(proof==json.loads(Path('.incident-staged/recovery-proof.json').read_text()),'uploaded proof differs')
   require(cp.get('producer_run_id')==rid and cp.get('db_sha256')==DB_SHA and manifest.get('generated_at')==STAMP and manifest.get('should_publish') is False and manifest.get('data_publishable') is False,'uploaded checkpoint contract')
 print(encode({'status':'VERIFIED_CHECKPOINT_ONLY_COLLECTION_HELD','run_id':rid,'artifact_id':item['id'],'db_sha256':DB_SHA,'original_observation_at':STAMP,'collection_resumed':False}))

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','claim','verify_upload']);args=parser.parse_args();globals()[args.action]()
