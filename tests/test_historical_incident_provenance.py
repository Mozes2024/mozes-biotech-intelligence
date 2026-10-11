"""A reviewed historical pre-claim failure must survive repinning, never replay."""
import copy
import json
from datetime import datetime, timezone
import pytest
from mozes import monitor_recovery as m

REPO = "Mozes2024/mozes-biotech-intelligence"
HEAD = "ba458d6cab5a97b38d923b4f8bb60ee19ce9377d"
BLOBS = {".github/workflows/lightweight-monitor.yml":"d8b2561dcdb6dd018d233b77b7376f4e55da19fe",
         "scripts/incident_recovery.py":"bf1bb33535bdd420c2770130436c6cbebda9d260",
         "scripts/restore_monitor_state.py":"5bffd761854b8e7ab01b64a51504c954c6d52331",
         "mozes/monitor_recovery.py":"864863aa29fa649d761cb5346faf247b951a17c9"}

@pytest.fixture
def evidence():
    run = {"id":38133079950,"head_sha":HEAD,"head_branch":"main","name":"lightweight-live-monitor",
           "head_repository":{"full_name":REPO},"status":"completed","conclusion":"failure",
           "event":"workflow_dispatch","run_attempt":1}
    names = [("Set up job","success"),("Run actions/checkout@v4","success"),
             ("Run actions/setup-python@v5","success"),("Run pip install -e .","success"),
             ("Verify incident recovery eligibility","failure"),("Acquire irreversible incident recovery claim","skipped"),
             ("Post Run actions/setup-python@v5","skipped"),("Post Run actions/checkout@v4","success"),("Complete job","success")]
    jobs = [{"name":"incident-eligibility","status":"completed","conclusion":"failure",
             "steps":[{"name":n,"status":"completed","conclusion":c} for n,c in names]},
            {"name":"monitor","status":"completed","conclusion":"skipped","steps":[]}]
    ancestry={"status":"ahead","merge_base_commit":{"sha":HEAD}}
    def gh(*args):
        path=args[1]
        if '/runs?' in path: return json.dumps({"workflow_runs":[run]})
        if '/jobs?' in path: return json.dumps({"total_count":len(jobs),"jobs":jobs})
        if '/compare/' in path: return json.dumps(ancestry)
        path=path.split('/contents/')[1].split('?')[0]
        return json.dumps({"type":"file","path":path,"sha":BLOBS[path]})
    return run,jobs,ancestry,gh

@pytest.mark.parametrize("pin", [HEAD,"dc06dcc172de4b504b2badba7d0ed010ee47ce3d","e"*40])
def test_reviewed_historical_failure_survives_repeated_repins(evidence,monkeypatch,pin):
    run,jobs,ancestry,gh=evidence
    monkeypatch.setenv("MOZES_INCIDENT_APPROVED_MAIN",pin)
    monkeypatch.delenv("GITHUB_RUN_ID",raising=False)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT","1")
    proof=m.audit_producers(REPO,datetime(2026,10,10,tzinfo=timezone.utc),gh)
    assert proof['audited_producers']==['38133079950']
    assert run['head_sha']==HEAD

@pytest.mark.parametrize("kind", ['unknown_sha','different_run','rerun','active','wrong_branch','wrong_repository',
                                  'claim_success','claim_failure','claim_missing','monitor_started','monitor_steps',
                                  'incomplete_jobs','incomplete_steps','uncompleted_step','unexpected_step','ancestry','blob'])
def test_historical_exception_rejects_unsafe_or_incomplete_evidence(evidence,monkeypatch,kind):
    run,jobs,ancestry,gh=evidence
    monkeypatch.setenv("MOZES_INCIDENT_APPROVED_MAIN","f"*40)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT","1")
    monkeypatch.delenv("GITHUB_RUN_ID",raising=False)
    if kind=='unknown_sha': run['head_sha']='a'*40
    elif kind=='different_run': run['id']=999
    elif kind=='rerun': run['run_attempt']=2
    elif kind=='active': run['status']='in_progress'
    elif kind=='wrong_branch': run['head_branch']='other'
    elif kind=='wrong_repository': run['head_repository']['full_name']='Other/Repo'
    elif kind.startswith('claim_'):
        if kind=='claim_missing': jobs[0]['steps'].pop(5)
        else: jobs[0]['steps'][5]['conclusion']=kind.split('_')[1]
    elif kind=='monitor_started': jobs[1]['conclusion']='failure'
    elif kind=='monitor_steps': jobs[1]['steps']=[{'name':'processing','conclusion':'success'}]
    elif kind=='incomplete_jobs': jobs.pop()
    elif kind=='incomplete_steps': jobs[0]['steps'].pop(0)
    elif kind=='uncompleted_step': jobs[0]['steps'][0]['status']='in_progress'
    elif kind=='unexpected_step': jobs[0]['steps'].append({'name':'write','conclusion':'success','status':'completed'})
    elif kind=='ancestry': ancestry['merge_base_commit']['sha']='a'*40
    elif kind=='blob':
        old=gh
        def gh(*args):
            result=json.loads(old(*args))
            if '/contents/' in args[1]: result['sha']='a'*40
            return json.dumps(result)
    with pytest.raises(RuntimeError):
        m.audit_producers(REPO,datetime(2026,10,10,tzinfo=timezone.utc),gh)
