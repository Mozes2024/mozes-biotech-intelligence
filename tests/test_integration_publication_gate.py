import hashlib
import os
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from mozes import db, pipeline_v2c as p, regulatory_review as review, edge_enrichment
from mozes.intelligence_store import state_get, state_put

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gate_helpers', ROOT / 'tests/test_regulatory_review.py')
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
spec = importlib.util.spec_from_file_location('gate_pages', ROOT / 'scripts/restore_pages_state.py')
pages = importlib.util.module_from_spec(spec); spec.loader.exec_module(pages)
SHA = os.environ.get('GITHUB_SHA', 'd' * 40)

@pytest.mark.parametrize('limit,reason', [
    ('MAX_PUBLIC_REVIEW_BYTES', 'regulatory_review_public_byte_budget'),
    ('MAX_PAGES_SITE_BYTES', 'regulatory_review_pages_site_budget')])
@pytest.mark.parametrize('status', ['OK', 'PARTIAL'])
def test_real_refusal_checkpoint_pages_and_later_recovery(tmp_path, monkeypatch, limit, reason, status):
    monkeypatch.chdir(tmp_path)
    private = tmp_path / '.monitor'; private.mkdir()
    database = private / 'mozes-live.db'
    monkeypatch.setattr(p, 'DB_PATH', database)
    monkeypatch.setenv('GITHUB_SHA', SHA)
    monkeypatch.setenv('GITHUB_RUN_ID', '77')
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'outputs'))
    public = tmp_path / 'site'; public.mkdir()
    for name in ('data.json', 'alerts.json', 'alerts-evidence-v1.json', 'updates.json', 'publish-receipt.json'):
        (public / name).write_text('previous-' + name)
    old_public = {x.name: x.read_bytes() for x in public.iterdir()}
    conn = db.connect(database); p.ensure_schema(conn)
    h.catalog(conn); h.pending(conn)
    state_put(conn, 'published_ledger', {'immutable': 'previous-receipt'})
    state_put(conn, 'publish_request', {'at': '2020-01-01T00:00:00+00:00', 'immutable': True})
    before_review = [tuple(x) for x in conn.execute('SELECT * FROM v2c_regulatory_review')]
    conn.close()
    def observation(conn):
        state_put(conn, 'gate_observation', {'durable': True})
        return {'status': status, 'details': {'synthetic': True}}
    monkeypatch.setattr(p, 'run_priority_pipeline', observation)
    started = datetime.now(timezone.utc) - timedelta(seconds=1)
    original = getattr(review, limit)
    monkeypatch.setattr(review, limit, 1)
    assert p.main(['monitor', '--priority-only', '--output', str(public / 'data.json')]) == 0
    manifest = json.loads((private / 'manifest.json').read_text())
    assert manifest['source_sha'] == SHA and manifest['runtime_status'] == status
    assert datetime.fromisoformat(manifest['generated_at'].replace('Z', '+00:00')) >= started
    assert manifest['data_publishable'] is False and manifest['should_publish'] is False
    assert manifest['export_reason'] == reason
    assert {x.name: x.read_bytes() for x in public.iterdir()} == old_public
    conn = db.connect(database)
    assert state_get(conn, 'gate_observation') == {'durable': True}
    assert state_get(conn, 'published_ledger') == {'immutable': 'previous-receipt'}
    assert state_get(conn, 'publish_request')['immutable'] is True
    assert [tuple(x) for x in conn.execute('SELECT * FROM v2c_regulatory_review')] == before_review
    conn.close()
    checkpoint = edge_enrichment.checkpoint(database)
    assert checkpoint['db_sha256'] == hashlib.sha256(database.read_bytes()).hexdigest()
    run = {'id': 77, 'head_sha': SHA, 'created_at': started.isoformat()}
    artifact = {'id': 88, 'created_at': datetime.now(timezone.utc).isoformat()}
    refused_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match='manifest_provenance'):
        pages.validate(private, run, artifact, datetime.now(timezone.utc))
    assert refused_hash == hashlib.sha256(database.read_bytes()).hexdigest()
    # Restore only the test budget. No review deletion, rewind or replay.
    monkeypatch.setattr(review, limit, original)
    assert p.main(['monitor', '--priority-only', '--output', str(public / 'data.json')]) == 0
    recovered = json.loads((public / 'data.json').read_text(encoding='utf-8'))
    assert recovered['regulatory_review']['count'] == 1
    assert recovered['build']['source_sha'] == SHA
    conn = db.connect(database)
    assert state_get(conn, 'gate_observation') == {'durable': True}
    assert state_get(conn, 'published_ledger') == {'immutable': 'previous-receipt'}
    assert [tuple(x) for x in conn.execute('SELECT * FROM v2c_regulatory_review')] == before_review
    conn.close()
    edge_enrichment.checkpoint(database)
    artifact['created_at'] = datetime.now(timezone.utc).isoformat()
    _, receipt = pages.validate(private, run, artifact, datetime.now(timezone.utc))
    assert receipt['source_run_id'] == '77'
