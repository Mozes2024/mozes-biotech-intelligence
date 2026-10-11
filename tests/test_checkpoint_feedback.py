import hashlib
import json
import sqlite3
import sys
import urllib.error
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

# `pytest`'s console entry point does not install the checkout-only scripts
# namespace. Scope its import path locally; the runtime uses python -m scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from scripts import checkpoint_feedback as feedback
finally:
    sys.path.pop(0)

NOW = datetime(2026, 10, 11, 20, tzinfo=timezone.utc)
SHA = 'a' * 40


@pytest.fixture
def relay(monkeypatch):
    for key, value in {'GITHUB_REPOSITORY':feedback.REPO,'GITHUB_REF':'refs/heads/main',
                       'GITHUB_RUN_ATTEMPT':'1','GITHUB_RUN_ID':'123','GITHUB_SHA':SHA,
                       'CHECKPOINT_ARTIFACT_ID':'456'}.items():
        monkeypatch.setenv(key,value)
    run = {'id':123,'head_sha':SHA,'head_branch':'main','run_attempt':1,
           'head_repository':{'full_name':feedback.REPO},'name':'lightweight-live-monitor',
           'path':'.github/workflows/lightweight-monitor.yml','event':'workflow_dispatch',
           'created_at':'2026-10-11T19:50:00Z'}
    monitor = {'name':'monitor','status':'completed','conclusion':'success','steps':[
        {'name':name,'status':'completed','conclusion':'success'} for name in
        ('Bind durable checkpoint to monitor database','Run actions/upload-artifact@v4')]}
    artifact = {'id':456,'name':'mozes-live-monitor','expired':False,'size_in_bytes':100,
                'created_at':'2026-10-11T19:59:30Z','workflow_run':{'id':123,'head_sha':SHA,'head_branch':'main'}}
    jobs = [monitor,{'name':'incident-eligibility','status':'completed','conclusion':'skipped'}]
    def api(path):
        if '/jobs?' in path:return {'total_count':len(jobs),'jobs':jobs}
        if '/artifacts?' in path:return {'total_count':1,'artifacts':[artifact]}
        return run
    monkeypatch.setattr(feedback,'api',api)
    def download(*args):
        root = Path(args[-1]);database = root/'mozes-live.db'
        with closing(sqlite3.connect(database)) as conn:
            conn.execute('CREATE TABLE test(id INTEGER PRIMARY KEY)')
            conn.commit()
        digest=hashlib.sha256(database.read_bytes()).hexdigest()
        (root/'edge-checkpoint.json').write_text(json.dumps({'schema':1,'producer_run_id':'123',
            'created_at':'2026-10-11T19:59:00Z','db_sha256':digest}))
        (root/'manifest.json').write_text(json.dumps({'schema':1,'source_sha':SHA,
            'data_publishable':True,'generated_at':'2026-10-11T19:58:50Z'}))
    monkeypatch.setattr(feedback,'gh',download)
    return run,monitor,artifact,jobs


def test_downloaded_artifact_proves_checkpoint_without_restoring(relay):
    receipt=feedback.evidence(now=NOW)
    assert receipt['run_id']=='123' and receipt['artifact_id']==456
    assert receipt['proof']=='actions_download_sha256_sqlite_v1'
    assert len(receipt['db_sha256'])==64


@pytest.mark.parametrize('target,field,value',[
    (0,'head_sha','b'*40),(0,'run_attempt',2),(0,'head_branch','other'),
    (1,'status','in_progress'),(1,'conclusion','failure'),
    (2,'expired',True),(2,'id',457),(2,'created_at','2026-10-11T20:01:00Z'),
])
def test_untrusted_or_incomplete_upload_never_becomes_receipt(relay,target,field,value):
    relay[target][field]=value
    with pytest.raises(RuntimeError):feedback.evidence(now=NOW)


def test_failed_upload_does_not_emit_evidence(relay):
    relay[1]['steps'][1]['conclusion']='failure'
    with pytest.raises(RuntimeError,match='checkpoint_step_failed'):feedback.evidence(now=NOW)


def test_incident_run_is_not_feedback_source(relay):
    relay[3][1]['conclusion']='success'
    with pytest.raises(RuntimeError,match='incident_not_excluded'):feedback.evidence(now=NOW)


def test_corrupt_download_fails_closed(relay,monkeypatch):
    original=feedback.gh
    def corrupt(*args):
        original(*args)
        (Path(args[-1])/'mozes-live.db').write_bytes(b'corrupt')
    monkeypatch.setattr(feedback,'gh',corrupt)
    with pytest.raises(RuntimeError,match='checkpoint_integrity'):feedback.evidence(now=NOW)


def test_stale_checkpoint_not_liveness(relay):
    with pytest.raises(RuntimeError,match='stale_or_future_checkpoint'):
        feedback.evidence(now=NOW.replace(hour=21))


@pytest.mark.parametrize('status',[301,302,307,401,403,429,500])
def test_http_failure_no_retry_no_secret_in_error(monkeypatch,status):
    monkeypatch.setenv('MOZES_EDGE_SYNC_URL',feedback.SYNC_URL)
    monkeypatch.setenv('EDGE_SYNC_TOKEN','offline-secret')
    calls=[]
    def opener(request,timeout):
        calls.append(request)
        assert request.full_url==feedback.TARGET and request.get_method()=='POST' and timeout==15
        raise urllib.error.HTTPError(request.full_url,status,'offline-secret',{},None)
    with pytest.raises(RuntimeError,match=f'^feedback_http_{status}$'):
        feedback.send({},opener=opener)
    assert len(calls)==1


def test_redirect_handler_never_forwards_secret():
    assert feedback.NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere') is None


def test_wrong_origin_does_not_send_secret(monkeypatch):
    monkeypatch.setenv('MOZES_EDGE_SYNC_URL','https://elsewhere/edge/sync')
    with pytest.raises(RuntimeError,match='edge_origin_mismatch'):
        feedback.send({},opener=lambda *a,**k:pytest.fail('network called'))


def test_timeout_is_sanitized(monkeypatch):
    monkeypatch.setenv('MOZES_EDGE_SYNC_URL',feedback.SYNC_URL)
    monkeypatch.setenv('EDGE_SYNC_TOKEN','offline-secret')
    def opener(*args,**kwargs):raise TimeoutError('offline-secret')
    with pytest.raises(RuntimeError,match='^feedback_network_or_timeout$'):
        feedback.send({},opener=opener)


@pytest.mark.parametrize('body',[b'{"ok":true,"status":"checkpointed"}',
                              b'{"ok":true,"status":"duplicate"}'])
def test_authenticated_success(monkeypatch,body):
    from io import BytesIO
    monkeypatch.setenv('MOZES_EDGE_SYNC_URL',feedback.SYNC_URL)
    monkeypatch.setenv('EDGE_SYNC_TOKEN','offline-secret')
    response=BytesIO(body);response.status=200
    feedback.send({},opener=lambda request,timeout:response)


@pytest.mark.parametrize('body,category',[(b'{}','feedback_response_shape'),
                                        (b'x'*4097,'feedback_response_size')])
def test_response_bounds_and_shape(monkeypatch,body,category):
    from io import BytesIO
    monkeypatch.setenv('MOZES_EDGE_SYNC_URL',feedback.SYNC_URL)
    monkeypatch.setenv('EDGE_SYNC_TOKEN','offline-secret')
    response=BytesIO(body);response.status=200
    with pytest.raises(RuntimeError,match=category):
        feedback.send({},opener=lambda request,timeout:response)


def test_failure_exit_code_does_not_expose_exception(monkeypatch,capsys):
    def fail():raise RuntimeError('private-token / arbitrary body')
    monkeypatch.setattr(feedback,'evidence',fail)
    assert feedback.main()==1
    assert capsys.readouterr().out=='checkpoint_feedback FAIL unexpected_validation_error\n'
