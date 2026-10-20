import hashlib
import importlib.util
import json
import sqlite3
import subprocess
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
spec = importlib.util.spec_from_file_location('restore_pages', Path(__file__).resolve().parents[1] / 'scripts/restore_pages_state.py')
pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)

NOW = datetime(2026, 10, 11, 16, tzinfo=timezone.utc)
SHA = 'a' * 40


def fixture(tmp_path, age=180):
    at = NOW - timedelta(minutes=age)
    source = tmp_path / 'mozes-live.db'
    with sqlite3.connect(source) as conn:
        conn.execute('CREATE TABLE preserved(id PRIMARY KEY,value)')
        conn.execute("INSERT INTO preserved VALUES('ACK-1','unchanged')")
    checkpoint = {'schema': 1, 'producer_run_id': '2', 'created_at': at.isoformat(),
                  'db_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    manifest = {'schema': 1, 'source_sha': SHA, 'generated_at': at.isoformat(), 'data_publishable': True}
    (tmp_path / 'edge-checkpoint.json').write_text(json.dumps(checkpoint))
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    run = {'id': 2, 'head_branch': 'main', 'head_repository': {'full_name': pages.REPO},
           'name': 'lightweight-live-monitor', 'path': '.github/workflows/lightweight-monitor.yml',
           'head_sha': SHA, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'success', 'event':'workflow_dispatch',
           'created_at': (at-timedelta(minutes=3)).isoformat(), 'updated_at': at.isoformat()}
    artifact = {'id': 7, 'workflow_run': {'id': 2, 'head_branch':'main', 'head_sha':SHA}, 'name': 'mozes-live-monitor', 'expired': False,
                'size_in_bytes': 100, 'created_at': at.isoformat()}
    return source, checkpoint, manifest, run, artifact


def mock_api(monkeypatch, run, artifact, *, extra=None, bound='success', artifacts=None):
    runs = [run] + (extra or [])
    artifacts = [artifact] if artifacts is None else artifacts
    def api(path):
        if path.startswith('artifacts?'):
            return {'total_count': len(artifacts), 'artifacts': artifacts}
        if path.startswith('workflows/'):
            return {'total_count': len(runs), 'workflow_runs': runs}
        return {'total_count': 1, 'jobs': [{'name': 'monitor', 'status': 'completed', 'steps': [
            {'name': 'Bind durable checkpoint to monitor database', 'conclusion': bound, 'status':'completed'},
            {'name': 'Run actions/upload-artifact@v4', 'conclusion': bound, 'status':'completed'}]}]}
    monkeypatch.setattr(pages, 'api', api)


@pytest.mark.parametrize('age,stale', [(5, False), (180, True)])
def test_age_is_display_metadata_not_producer_guard(tmp_path, age, stale):
    source, _, _, run, artifact = fixture(tmp_path, age)
    before = source.read_bytes()
    _, receipt = pages.validate(tmp_path, run, artifact, NOW)
    assert receipt['stale'] is stale
    assert receipt['data_age_seconds'] == age * 60
    assert source.read_bytes() == before


@pytest.mark.parametrize('field,value', [('db_sha256','0'*64), ('producer_run_id','1'), ('schema',2),
                                      ('created_at','invalid'), ('created_at','2026-10-12T00:00:00Z')])
def test_corrupt_checkpoint_fails(tmp_path, field, value):
    _, checkpoint, _, run, artifact = fixture(tmp_path)
    checkpoint[field] = value
    (tmp_path/'edge-checkpoint.json').write_text(json.dumps(checkpoint))
    with pytest.raises(RuntimeError):
        pages.validate(tmp_path, run, artifact, NOW)


@pytest.mark.parametrize('field,value', [('source_sha','b'*40), ('schema',2), ('data_publishable',False),
                                      ('generated_at','2026-10-12T00:00:00Z')])
def test_manifest_provenance(tmp_path, field, value):
    _, _, manifest, run, artifact = fixture(tmp_path)
    manifest[field] = value
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError):
        pages.validate(tmp_path, run, artifact, NOW)


@pytest.mark.parametrize('field,value', [('head_branch','other'), ('name','deep-monitor'),
    ('head_repository',{'full_name':'attacker/repo'}), ('path','other.yml'), ('run_attempt',2)])
def test_wrong_producer(tmp_path, monkeypatch, field, value):
    _, _, _, run, artifact = fixture(tmp_path)
    run[field] = value
    mock_api(monkeypatch, run, artifact)
    with pytest.raises(RuntimeError): pages.newest()


def test_newest_expired_never_falls_back(tmp_path, monkeypatch):
    _, _, _, run, artifact = fixture(tmp_path)
    artifact['expired'] = True
    older = deepcopy(artifact); older.update(id=6, expired=False, created_at=(NOW-timedelta(days=1)).isoformat())
    mock_api(monkeypatch, run, artifact, artifacts=[artifact, older])
    with pytest.raises(RuntimeError, match='expired'): pages.newest()


def test_successful_upload_missing_artifact_blocks(tmp_path, monkeypatch):
    _, _, _, run, artifact = fixture(tmp_path)
    newer = deepcopy(run); newer.update(id=3, updated_at=NOW.isoformat())
    mock_api(monkeypatch, run, artifact, extra=[newer])
    with pytest.raises(RuntimeError, match='missing_or_ambiguous'): pages.newest()


def test_older_dispatched_waiter_with_newer_artifact_owns_snapshot(tmp_path, monkeypatch):
    _, _, _, run, artifact = fixture(tmp_path)
    newer_dispatch = deepcopy(run)
    newer_dispatch.update(id=3, created_at=(NOW-timedelta(minutes=150)).isoformat())
    older_artifact = deepcopy(artifact)
    older_artifact.update(id=6, created_at=(NOW-timedelta(minutes=200)).isoformat(), workflow_run={'id':3})
    mock_api(monkeypatch,run,artifact,extra=[newer_dispatch],artifacts=[older_artifact,artifact])
    assert pages.newest()[1]['id'] == artifact['id']


def test_no_artifact_cannot_be_green_health(tmp_path, monkeypatch):
    _, _, _, run, artifact = fixture(tmp_path)
    mock_api(monkeypatch,run,artifact,artifacts=[])
    with pytest.raises(RuntimeError,match='artifact_inventory'): pages.newest()


def test_incomplete_jobs_block(tmp_path, monkeypatch):
    _, _, _, run, artifact = fixture(tmp_path)
    run['status']='in_progress'
    mock_api(monkeypatch,run,artifact)
    with pytest.raises(RuntimeError,match='producer_busy'): pages.newest()


def test_provider_inventory_error_is_not_fallback(monkeypatch):
    def failed(path): raise RuntimeError('http_403')
    monkeypatch.setattr(pages,'api',failed)
    with pytest.raises(RuntimeError,match='http_403'): pages.newest()


def test_duplicate_artifact_layout_rejected(tmp_path):
    _, _, _, run, artifact = fixture(tmp_path)
    nested=tmp_path/'nested'; nested.mkdir()
    (nested/'edge-checkpoint.json').write_text('{}')
    with pytest.raises(RuntimeError,match='artifact_layout'): pages.validate(tmp_path,run,artifact,NOW)


def test_old_requested_run_is_advisory_and_no_producer_state(tmp_path, monkeypatch):
    incoming = tmp_path/'incoming'; incoming.mkdir()
    source, _, _, run, artifact = fixture(incoming)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_REPOSITORY',pages.REPO)
    monkeypatch.setenv('SOURCE_RUN_ID','1')
    monkeypatch.setenv('MOZES_DB_PATH',str(tmp_path/'consumer.db'))
    mock_api(monkeypatch, run, artifact)
    def download(*args, **kwargs):
        import shutil
        root = Path(args[-1])
        for p in incoming.iterdir(): shutil.copyfile(p,root/p.name)
        return ''
    monkeypatch.setattr(pages,'gh',download)
    receipt = pages.restore(now=NOW)
    assert receipt['source_run_id'] == '2'
    assert (tmp_path/'consumer.db').read_bytes() == source.read_bytes()
    assert not (tmp_path/'.monitor/edge-checkpoint.json').exists()
    assert not (tmp_path/'.monitor/manifest.json').exists()


def test_read_only_export_preserves_database_and_authoritative_state(tmp_path, monkeypatch):
    from mozes import db
    from mozes.intelligence_store import ensure_schema
    from mozes.radar import bootstrap_database
    from mozes import pipeline_v2c
    monkeypatch.chdir(tmp_path)
    path = tmp_path/'database.db'
    conn = db.connect(path); ensure_schema(conn); bootstrap_database(conn); conn.close()
    before = path.read_bytes()
    metadata = tmp_path/'.monitor'; metadata.mkdir()
    receipt = {'schema':1,'consumer_only':True,'checkpoint_at':(datetime.now(timezone.utc)-timedelta(hours=3)).isoformat(),
               'db_sha256':hashlib.sha256(before).hexdigest(),'stale':True,'data_age_seconds':10800}
    (metadata/'pages-consumer.json').write_text(json.dumps(receipt))
    monkeypatch.setattr(pipeline_v2c,'DB_PATH',path)
    assert not (tmp_path/'web/data.json').exists()
    assert pipeline_v2c.main(['export','--consumer-only']) == 0
    payload = json.loads((tmp_path/'web/data.json').read_text(encoding='utf-8'))
    assert payload['live'], 'The isolated database must supply the dashboard content'
    assert payload['data_stale'] is True and payload['data_age_seconds'] >= 10800
    assert path.read_bytes() == before
    assert list(metadata.iterdir()) == [metadata/'pages-consumer.json']


def test_browser_freshness_ages_independently_of_export_time():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node','--input-type=module','-e', """
      import assert from 'node:assert/strict';
      await import('./web/checkpoint_health.js');
      const now=Date.parse('2026-10-11T16:00:00Z');
      assert.equal(MozesCheckpointHealth({checkpoint_at:'2026-10-11T15:55:00Z'},now).stale,false);
      assert.equal(MozesCheckpointHealth({checkpoint_at:'2026-10-11T13:00:00Z'},now).age_minutes,180);
      assert.equal(MozesCheckpointHealth({checkpoint_at:'2026-10-11T15:14:59Z'},now).stale,true);
      for(const checkpoint_at of [null,'bad','2026-10-12T00:00:00Z','2026-10-11T15:55:00'])
        assert.equal(MozesCheckpointHealth({checkpoint_at},now).stale,true);
    """], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_complete_inventory_beyond_old_lifetime_limit(monkeypatch):
    rows = [{'id': i} for i in range(1, 3102)]
    visited = []
    def api(path):
        page = int(path.rsplit('=', 1)[1]); visited.append(page)
        return {'total_count': len(rows), 'workflow_runs': rows[(page-1)*100:page*100]}
    monkeypatch.setattr(pages, 'api', api)
    assert pages.inventory('workflows/x/runs?exclude_pull_requests=true', 'workflow_runs') == rows
    assert visited == list(range(1, 33))


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'count_changed', 'time_budget'])
def test_complete_inventory_fails_closed(monkeypatch, problem):
    calls = 0
    def api(path):
        nonlocal calls
        calls += 1
        rows = [{'id': i} for i in range(1, 101)] if calls == 1 else [{'id': 101}]
        if problem == 'missing' and calls == 2: rows = []
        if problem == 'duplicate' and calls == 2: rows = [{'id': 1}]
        return {'total_count': 102 if problem == 'count_changed' and calls == 2 else 101,
                'workflow_runs': rows}
    monkeypatch.setattr(pages, 'api', api)
    if problem == 'time_budget':
        ticks = iter([0, 0, 91]); monkeypatch.setattr(pages.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(RuntimeError):
        pages.inventory('workflows/x/runs?', 'workflow_runs')


@pytest.mark.parametrize('status', pages.ACTIVE)
def test_old_queued_owner_cannot_be_omitted(tmp_path, monkeypatch, status):
    _, _, _, run, artifact = fixture(tmp_path)
    old = deepcopy(run); old.update(id=1, status=status, updated_at=(NOW-timedelta(days=20)).isoformat())
    mock_api(monkeypatch, run, artifact, extra=[old])
    with pytest.raises(RuntimeError, match='producer_busy'): pages.newest()


def test_old_rerun_with_recent_update_requires_review(tmp_path, monkeypatch):
    _, _, _, run, artifact = fixture(tmp_path)
    old = deepcopy(run)
    old.update(id=1, run_attempt=2, created_at=(NOW-timedelta(days=20)).isoformat(), updated_at=NOW.isoformat())
    mock_api(monkeypatch, run, artifact, extra=[old])
    with pytest.raises(RuntimeError, match='rerun_requires_review'): pages.newest()


@pytest.mark.parametrize('problem,category', [('queued', 'producer_busy'),
    ('rerun', 'rerun_requires_review'), ('unknown', 'incomplete_inventory')])
def test_old_potential_writer_beyond_three_thousand_is_audited(tmp_path, monkeypatch, problem, category):
    _, _, _, run, artifact = fixture(tmp_path)
    historic = deepcopy(run); historic['updated_at'] = (NOW-timedelta(days=20)).isoformat()
    rows = [{**historic, 'id': i} for i in range(100, 3201)]
    old = {**historic, 'id': 1}
    if problem == 'queued': old['status'] = 'queued'
    elif problem == 'rerun': old.update(run_attempt=2, updated_at=NOW.isoformat())
    else: old['status'] = 'unknown'
    rows.extend([run, old])
    def api(path):
        if path.startswith('artifacts?'): return {'total_count': 1, 'artifacts': [artifact]}
        if path.startswith('workflows/'):
            page = int(path.rsplit('=', 1)[1])
            return {'total_count': len(rows), 'workflow_runs': rows[(page-1)*100:page*100]}
        return {'total_count': 1, 'jobs': [{'name': 'monitor', 'status': 'completed', 'steps': [
            {'name': name, 'status': 'completed', 'conclusion': 'success'} for name in
            ['Bind durable checkpoint to monitor database', 'Run actions/upload-artifact@v4']]}]}
    monkeypatch.setattr(pages, 'api', api)
    with pytest.raises(RuntimeError, match=category): pages.newest()


def test_changed_checkpoint_download_is_discarded_before_reselection(tmp_path, monkeypatch):
    import shutil
    first = tmp_path/'first'; first.mkdir()
    second = tmp_path/'second'; second.mkdir()
    source_a, _, _, run_a, artifact_a = fixture(first)
    source_b, checkpoint_b, _, run_b, artifact_b = fixture(second)
    with sqlite3.connect(source_b) as conn:
        conn.execute("INSERT INTO preserved VALUES('NEW','new authoritative state')")
    run_b['id'] = 3; artifact_b['id'] = 8; artifact_b['workflow_run']['id'] = 3
    checkpoint_b.update(producer_run_id='3', db_sha256=hashlib.sha256(source_b.read_bytes()).hexdigest())
    (second/'edge-checkpoint.json').write_text(json.dumps(checkpoint_b))
    selections = iter([(run_a, artifact_a), (run_b, artifact_b), (run_b, artifact_b), (run_b, artifact_b)])
    monkeypatch.setattr(pages, 'newest', lambda: next(selections))
    downloads = []
    def download(*args, **kwargs):
        directory = first if args[2] == '2' else second
        downloads.append(args[2])
        assert not (tmp_path/'consumer.db').exists()
        for path in directory.iterdir(): shutil.copyfile(path, Path(args[-1])/path.name)
        return ''
    monkeypatch.setattr(pages, 'gh', download)
    monkeypatch.setattr(pages.time, 'sleep', lambda seconds: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_REPOSITORY', pages.REPO)
    monkeypatch.setenv('MOZES_DB_PATH', str(tmp_path/'consumer.db'))
    receipt = pages.restore(now=NOW)
    assert downloads == ['2', '3'] and receipt['artifact_id'] == 8
    assert (tmp_path/'consumer.db').read_bytes() == source_b.read_bytes()
    assert source_a.read_bytes() != source_b.read_bytes()


@pytest.mark.parametrize('category', ['producer_busy', 'checkpoint_changed', 'inventory_changed'])
def test_transient_conflict_reselects_with_bounded_wait(monkeypatch, category):
    calls, waits = [], []
    def attempt(*, now):
        calls.append(now)
        if len(calls) < 3: raise RuntimeError(category)
        return {'artifact_id': 99}
    monkeypatch.setattr(pages, '_restore_once', attempt)
    monkeypatch.setattr(pages.time, 'sleep', waits.append)
    assert pages.restore() == {'artifact_id': 99}
    assert len(calls) == 3 and waits == [20, 20]


def test_busy_retry_exhaustion_cannot_publish(monkeypatch, tmp_path):
    calls = []
    def attempt(*, now):
        calls.append(now); raise RuntimeError('producer_busy')
    monkeypatch.setattr(pages, '_restore_once', attempt)
    monkeypatch.setattr(pages.time, 'sleep', lambda seconds: None)
    with pytest.raises(RuntimeError, match='producer_busy'): pages.restore()
    assert len(calls) == 3
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('category', ['incomplete_inventory', 'incomplete_producer',
    'incomplete_checkpoint', 'checkpoint_integrity', 'artifact_provenance', 'sqlite_integrity',
    'rerun_requires_review', 'inventory_time_budget', 'http_403'])
def test_mandatory_evidence_failure_is_not_retried(monkeypatch, category):
    def attempt(*, now): raise RuntimeError(category)
    monkeypatch.setattr(pages, '_restore_once', attempt)
    monkeypatch.setattr(pages.time, 'sleep', lambda seconds: pytest.fail('unsafe retry'))
    with pytest.raises(RuntimeError, match=category): pages.restore()


def test_consumer_updates_bind_checkpoint_identity_without_changing_semantic_revision(tmp_path):
    from mozes import db
    from mozes.intelligence_store import ensure_schema
    from mozes.radar import bootstrap_database
    from mozes.pipeline_v2c import export_payload
    path=tmp_path/'source.db';conn=db.connect(path);ensure_schema(conn);bootstrap_database(conn)
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    changes=conn.total_changes;conn.execute('PRAGMA query_only=ON')
    before={'schema':1,'consumer_only':True,'source_run_id':'1','artifact_id':1,'db_sha256':digest,
            'checkpoint_at':(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat(),
            'data_age_seconds':300,'stale':False}
    first=export_payload(conn,tmp_path/'first/data.json',consumer=before)
    after={**before,'source_run_id':'2','artifact_id':2,
           'checkpoint_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),'data_age_seconds':60}
    second=export_payload(conn,tmp_path/'second/data.json',consumer=after)
    update=json.loads((tmp_path/'second/updates.json').read_text(encoding='utf-8'))
    assert first['build']['snapshot_revision']==second['build']['snapshot_revision']==update['revision']
    assert update['schema']==1
    assert update['checkpoint']=={key:second['consumer_checkpoint'][key] for key in
                                 ('checkpoint_at','source_run_id','artifact_id','db_sha256')}
    assert first['checkpoint_at']!=second['checkpoint_at']
    assert conn.total_changes==changes
    conn.close();assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
