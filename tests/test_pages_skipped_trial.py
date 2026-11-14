from copy import deepcopy
import pytest
from test_pages_consumer import pages,fixture,mock_api

def arrange(tmp_path,monkeypatch):
 _,_,_,run,artifact=fixture(tmp_path)
 skipped=deepcopy(run);skipped.update(id=3,conclusion='skipped',updated_at='2026-10-11T16:00:00+00:00')
 jobs=[{'name':n,'status':'completed','conclusion':'skipped','steps':[]}for n in ['nov14-checkpoint-only','incident-eligibility','prospective-pilot-claim','monitor','prospective-pilot-cleanup']]
 mock_api(monkeypatch,run,artifact,extra=[skipped]);original=pages.api
 monkeypatch.setattr(pages,'api',lambda path:{'total_count':len(jobs),'jobs':jobs}if path.startswith('runs/3/')else original(path))
 return run,artifact,skipped,jobs

def test_exact_no_step_skipped_trial_waiter_not_a_writer(tmp_path,monkeypatch):
 _,artifact,_,_=arrange(tmp_path,monkeypatch);assert pages.newest()[1]['id']==artifact['id']

@pytest.mark.parametrize('mutation',['empty','missing','duplicate','unknown','steps','success','active'])
def test_unproven_skip_still_blocks(tmp_path,monkeypatch,mutation):
 _,_,_,jobs=arrange(tmp_path,monkeypatch)
 if mutation=='empty':jobs.clear()
 if mutation=='missing':jobs.pop()
 if mutation=='duplicate':jobs[-1]['name']='monitor'
 if mutation=='unknown':jobs[-1]['name']='unknown'
 if mutation=='steps':jobs[0]['steps']=[{'name':'write','conclusion':'success'}]
 if mutation=='success':jobs[0]['conclusion']='success'
 if mutation=='active':jobs[0]['status']='in_progress'
 with pytest.raises(RuntimeError,match='unproven_skipped'):pages.newest()

def test_skipped_owner_cannot_be_used(tmp_path,monkeypatch):
 _,artifact,_,jobs=arrange(tmp_path,monkeypatch);artifact['workflow_run']['id']=3
 with pytest.raises(RuntimeError):pages.newest()

def test_unknown_conclusion_still_blocks(tmp_path,monkeypatch):
 _,_,skip,_=arrange(tmp_path,monkeypatch);skip['conclusion']='startup_failure'
 with pytest.raises(RuntimeError,match='unknown_producer_conclusion'):pages.newest()
