"""A presentation budget must not leave newly observed data with an old manifest."""
import json

import pytest

from mozes import db, pipeline_v2c as pipeline
from mozes.intelligence_store import state_get, state_put


@pytest.mark.parametrize('reason', ['regulatory_review_public_byte_budget', 'regulatory_review_pages_site_budget'])
@pytest.mark.parametrize('source_status', ['OK', 'PARTIAL'])
def test_public_export_refusal_preserves_observations_and_refreshes_private_manifest(tmp_path, monkeypatch, reason, source_status):
    monkeypatch.chdir(tmp_path)
    monitor = tmp_path / '.monitor'; monitor.mkdir()
    manifest_path = monitor / 'manifest.json'
    manifest_path.write_text(json.dumps({'schema': 1, 'source_sha': 'old-code',
                                         'generated_at': '2026-01-01', 'data_publishable': True}))
    public = tmp_path / 'data.json'; public.write_text('previous-publication')
    database = monitor / 'synthetic.db'
    monkeypatch.setattr(pipeline, 'DB_PATH', database)
    monkeypatch.setenv('GITHUB_SHA', 'new-reviewed-code')
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'outputs'))
    monkeypatch.setenv('MOZES_RESULT_PATH', str(tmp_path / 'result.json'))

    def fake_observation(conn):
        state_put(conn, 'fixture_observation', {'saved': True})
        return {'status': source_status, 'details': {'offline': True}}

    def refused_export(*args, **kwargs):
        raise ValueError(reason + ' exceeded; all rows retained; publication stopped')

    monkeypatch.setattr(pipeline, 'run_priority_pipeline', fake_observation)
    monkeypatch.setattr(pipeline, 'export_payload', refused_export)
    assert pipeline.main(['monitor', '--priority-only', '--output', str(public)]) == 0
    manifest = json.loads(manifest_path.read_text())
    assert manifest['source_sha'] == 'new-reviewed-code'
    assert manifest['generated_at'] != '2026-01-01'
    assert manifest['data_publishable'] is False and manifest['should_publish'] is False
    assert manifest['runtime_status'] == source_status
    assert manifest['pipeline_complete'] is (source_status == 'OK')
    assert manifest['export_status'] == 'REFUSED' and manifest['export_reason'] == reason
    assert (tmp_path / 'outputs').read_text() == 'should_publish=false\n'
    result = json.loads((tmp_path / 'result.json').read_text())
    assert result['manifest'] == manifest and result['status'] == source_status
    conn = db.connect(database)
    assert state_get(conn, 'fixture_observation') == {'saved': True}
    conn.close()
    assert public.read_text() == 'previous-publication'


def test_unrelated_export_failure_is_not_masked_as_budget_refusal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, 'DB_PATH', tmp_path / 'synthetic.db')
    monkeypatch.setattr(pipeline, 'run_priority_pipeline', lambda conn: {'status': 'OK'})
    def fail(*args, **kwargs): raise ValueError('unrelated_source_integrity_error')
    monkeypatch.setattr(pipeline, 'export_payload', fail)
    with pytest.raises(ValueError, match='unrelated_source_integrity_error'):
        pipeline.main(['monitor', '--priority-only'])
    assert not (tmp_path / '.monitor/manifest.json').exists()
