"""The one-artifact exception never loosens ordinary lineage gates."""
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mozes import november_lineage as n
from mozes import monitor_recovery as recovery


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    source = tmp_path / 'source.db'
    conn = sqlite3.connect(source)
    for t in ('change_events', 'alert_outbox', 'edge_event_links'):
        conn.execute(f'CREATE TABLE {t}(id TEXT)')
        conn.execute(f'INSERT INTO {t} VALUES (?)', ('durable',))
    conn.commit(); conn.close()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(n, 'DIGEST', digest)
    monkeypatch.setattr(n, 'ANCESTOR_DIGEST', digest)
    checkpoint = {'schema': 1, 'producer_run_id': n.SOURCE, 'created_at': n.STAMP, 'db_sha256': digest}
    manifest = {'source_sha': n.HEAD, 'pipeline_complete': True, 'data_publishable': True,
                'generated_at': '2026-11-01T08:23:52+00:00'}
    state = {'latest': n.SOURCE, 'artifact': n.ARTIFACT, 'expired': False, 'run_attempt': 1,
             'count': 2, 'receipt': True, 'write': False, 'latest_calls': 0, 'replace_after_receipts': False, 'ancestor_bad': False, 'ancestor_subset_bad': False}
    def latest():
        state['latest_calls'] += 1
        return [{'databaseId': state['latest']}]
    def gh(*args, **kw):
        path = args[1]
        if '/artifacts?' in path:
            return json.dumps({'total_count': 1, 'artifacts': [{'id': state['artifact'], 'expired': state['expired'],
                'name': 'mozes-live-monitor', 'workflow_run': {'head_sha': n.HEAD}}]})
        if '/workflows/' in path:
            return json.dumps({'total_count': state['count'], 'workflow_runs': [
                {'id': int(n.SOURCE), 'updated_at': n.STAMP, 'status': 'completed'},
                {'id': 900, 'updated_at': '2026-11-02T08:00:00Z', 'status': 'completed'}]})
        run_id = path.rsplit('/', 1)[-1]
        return json.dumps({'id': int(run_id), 'head_branch': 'main', 'head_repository': {'full_name': n.REPO},
            'name': 'lightweight-live-monitor', 'head_sha': n.HEAD, 'status': 'completed',
            'conclusion': 'success', 'run_attempt': state['run_attempt']})
    def download(run_id, dest):
        shutil.copy2(source, Path(dest) / 'mozes-live.db')
        if state['ancestor_bad']:
            (Path(dest) / 'mozes-live.db').write_bytes(b'wrong')
        if state['ancestor_subset_bad']:
            conn = sqlite3.connect(Path(dest) / 'mozes-live.db')
            conn.execute("INSERT INTO change_events VALUES ('missing')")
            conn.commit(); conn.close()
            monkeypatch.setattr(n, 'ANCESTOR_DIGEST', hashlib.sha256((Path(dest) / 'mozes-live.db').read_bytes()).hexdigest())
        (Path(dest) / 'edge-checkpoint.json').write_text(json.dumps({'schema': 1, 'producer_run_id': n.ANCESTOR,
            'created_at': '2026-11-01T07:59:46+00:00', 'db_sha256': n.ANCESTOR_DIGEST}))
    def audit(repo, since, runner, **kw):
        rows = json.loads(runner('api', '/actions/workflows/lightweight-monitor.yml/runs?page=1'))['workflow_runs']
        assert len(rows) == 1 and rows[0]['id'] == 900
        if state['write']:
            raise RuntimeError('possible producer write')
        return {'audited_producers': ['900']}
    def receipts(path):
        if not state['receipt']:
            raise RuntimeError('Edge completion receipt differs')
        if state['replace_after_receipts']:
            state['latest'] = '999'
        return {'verified_edge_completions': 128}
    monkeypatch.setattr(recovery, 'audit_producers', audit)
    monkeypatch.setattr(recovery, 'verify_durable_history', receipts)
    def verify():
        return n.verify(n.REPO, n.SOURCE, source, checkpoint, manifest, gh, download, latest,
                        now=datetime(2026, 11, 2, tzinfo=timezone.utc))
    return source, checkpoint, manifest, state, verify


def test_exact_lineage_verified_with_final_newest_check(evidence):
    source, cp, manifest, state, verify = evidence
    proof = verify()
    assert proof['verified_edge_completions'] == 128 and state['latest_calls'] == 2
    assert proof['inventory_count'] == 2


@pytest.mark.parametrize('key,value', [('latest','999'), ('artifact', 999), ('expired',True),
                                      ('run_attempt',2), ('count',3), ('receipt',False),
                                      ('write',True), ('replace_after_receipts',True), ('ancestor_bad',True), ('ancestor_subset_bad',True)])
def test_remote_ambiguity_fails_closed(evidence,key,value):
    source, cp, manifest, state, verify = evidence
    state[key] = value
    with pytest.raises(RuntimeError): verify()


def test_checkpoint_time_or_hash_cannot_be_offset(evidence):
    source, cp, manifest, state, verify = evidence
    cp['created_at'] = '2026-11-01T09:23:53+00:00'
    with pytest.raises(RuntimeError, match='checkpoint mismatch'): verify()


def test_manifest_source_must_match(evidence):
    source, cp, manifest, state, verify = evidence
    manifest['source_sha'] = 'other'
    with pytest.raises(RuntimeError, match='manifest mismatch'): verify()


def test_different_source_hash_repo_or_run_ineligible(evidence):
    source, cp, manifest, state, verify = evidence
    assert not n.eligible('other/repo', n.SOURCE, source)
    assert not n.eligible(n.REPO, n.ANCESTOR, source)
    source.write_bytes(b'changed')
    assert not n.eligible(n.REPO, n.SOURCE, source)
