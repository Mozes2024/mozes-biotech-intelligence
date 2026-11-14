"""Local candidate: preserve the Nov14 incident decision during later restores.

This module does not grant restart authority or classify unknown effects as absent.
"""
from __future__ import annotations
import hashlib,json,shutil
from pathlib import Path
SOURCE='43622484709'
DB_SHA='afc818a17055937baff4d1263abb53a1688978a439d74b2d1164b8d3d7b1f2b2'
PROOF_SHA='25dfb883f0ebed858089dfa66806bf4c0cc0e295d59899b750690f47e0928d42'
CP_SHA='b2d2861b7e3796eb78a1288474d53ff6cc14cecf040ad8c1d8591f4f3f785664'
MANIFEST_SHA='a47220255acdee44e41040e0851e631892aeb74d6a840d1a78d3e1101190773d'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(ok,reason):
 if not ok:raise ValueError('Nov14 incident history: '+reason)
def historical_proof(source:Path,candidate:str):
 """Return only a hash-pinned historical decision, never a current no-write claim."""
 proof=source.with_name('recovery-proof.json')
 if str(candidate)==SOURCE:
  require(sha(source)==DB_SHA,'initial checkpoint DB changed')
  require(source.with_name('edge-checkpoint.json').is_file() and sha(source.with_name('edge-checkpoint.json'))==CP_SHA,'initial checkpoint identity changed')
  require(source.with_name('manifest.json').is_file() and sha(source.with_name('manifest.json'))==MANIFEST_SHA,'initial nonpublishable manifest changed')
  require(proof.is_file(),'accepted uncertainty ledger missing')
 if not proof.is_file():return None
 value=json.loads(proof.read_text())
 if str(candidate)!=SOURCE and value.get('checkpoint_only') is not True:return None
 require(sha(proof)==PROOF_SHA,'accepted uncertainty ledger changed')
 require(value.get('collection_resumed') is False and value.get('source_observation_freshness_advanced') is False,'historical decision shape changed')
 # These false fields describe the checkpoint-only phase, not the later collector.
 # Copy the exact original bytes; never replace the incident decision with no-write.
 return proof

REPO='Mozes2024/mozes-biotech-intelligence'
ARTIFACT=14124378764
ZIP_SHA='e55e8b607dcef31c68a6919e6b7bd96cc20714e869364077b770f4bc7b9c3da9'
def api(path):
 import subprocess
 return json.loads(subprocess.check_output(['gh','api',f'repos/{REPO}/{path}'],text=True,timeout=30))
def context():
 import os,re
 approved=os.environ.get('MOZES_NOV14_RESTART_SHA','');mode=os.environ.get('MOZES_NOV14_RESTART_MODE','')
 require(re.fullmatch('[a-f0-9]{40}',approved) is not None,'restart SHA missing')
 require(os.environ.get('GITHUB_REPOSITORY')==REPO and os.environ.get('GITHUB_REF')=='refs/heads/main' and os.environ.get('GITHUB_SHA')==approved and os.environ.get('GITHUB_RUN_ATTEMPT')=='1','restart identity')
 require(os.environ.get('GITHUB_WORKFLOW')=='lightweight-live-monitor' and os.environ.get('GITHUB_JOB')=='monitor' and os.environ.get('GITHUB_EVENT_NAME') in ('workflow_dispatch','schedule'),'restart workflow')
 require(mode in ('trial','continuous'),'restart mode')
 require(mode!='trial' or (os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and os.environ.get('NOV14_RESTART_INPUT')=='one-ordinary-pass'),'trial input')
 require(api('commits/main')['sha']==approved,'main changed')
 runs=api('actions/workflows/ci.yml/runs?head_sha='+approved+'&per_page=5')['workflow_runs']
 require(any(r.get('head_sha')==approved and r.get('head_branch')=='main' and r.get('event')=='push' and r.get('status')=='completed' and r.get('conclusion')=='success' for r in runs),'restart main CI not green')
 return mode

def preflight():
 if context()!='trial':return
 from scripts.restore_monitor_state import _list_success_runs
 require(str(_list_success_runs()[0]['databaseId'])==SOURCE,'trial source superseded')
 artifact=api(f'actions/artifacts/{ARTIFACT}')
 require(artifact.get('expired') is False and artifact.get('digest')=='sha256:'+ZIP_SHA and str(artifact.get('workflow_run',{}).get('id'))==SOURCE,'trial artifact unavailable or changed')
 print('NOV14_TRIAL_SOURCE_READ_ONLY_PREFLIGHT_PASS')

def before_write():
 import os
 if context()!='trial':return
 source=Path(os.environ.get('MOZES_DB_PATH','.monitor/mozes-live.db')).resolve()
 require(sha(source)==DB_SHA,'trial restored DB changed before collection')
 # The restore deliberately does not copy edge-checkpoint.json. Check the retained
 # fixed proof and exact original manifest again without pretending durability is age.
 require(sha(source.with_name('manifest.json'))==MANIFEST_SHA and sha(source.with_name('recovery-proof.json'))==PROOF_SHA,'restored incident history changed')
 # Full completion-time inventory, even within the ordinary 45-minute window.
 # Old-created producers finishing after the new boundary must not disappear.
 from datetime import datetime
 from mozes.monitor_recovery import audit_producers
 from scripts.restore_monitor_state import gh,_list_success_runs
 audit_producers(REPO,datetime.fromisoformat('2026-11-14T17:44:49.340200+00:00'),gh,full_history=True,source_run_id=SOURCE)
 require(str(_list_success_runs()[0]['databaseId'])==SOURCE,'trial source changed before collection')
 from mozes.nov14_checkpoint_only import read_provider,verify_provider
 verify_provider(source,read_provider())
 print('NOV14_TRIAL_FIXED_D1_RECEIPTS_PASS_BEFORE_FIRST_COLLECTION_WRITE')

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['preflight','before_write']);args=parser.parse_args();globals()[args.action]()
