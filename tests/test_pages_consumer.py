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
    with pytest.raises(RuntimeError,match='incomplete_checkpoint'): pages.newest()


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
    def download(*args):
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
    assert pipeline_v2c.main(['export','--consumer-only']) == 0
    payload = json.loads((tmp_path/'web/data.json').read_text(encoding='utf-8'))
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
