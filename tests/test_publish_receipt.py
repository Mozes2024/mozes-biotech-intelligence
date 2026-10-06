"""Deployment acknowledgement is trusted separately from a producer's request."""
import importlib.util
import json
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'restore_monitor_state.py'


def module():
    spec = importlib.util.spec_from_file_location('restore_monitor', SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_receipt_only_restores_successful_first_party_deployment(tmp_path, monkeypatch):
    script = module()
    monkeypatch.chdir(tmp_path)
    def gh(*args):
        if args[:2] == ('run', 'list'):
            return json.dumps([{'databaseId': 1}, {'databaseId': 2}])
        if args[0] == 'api':
            return json.dumps({'head_branch': 'main', 'name': 'deploy-pages',
                               'head_repository': {'full_name': 'Owner/Repo'},
                               'conclusion': 'failure' if args[1].endswith('/1') else 'success'})
        assert args[:3] == ('run', 'download', '2')
        Path(args[-1], 'publish-receipt.json').write_text(json.dumps({'change_ledger': 'a' * 64}))
        return ''
    monkeypatch.setattr(script, 'gh', gh)
    script.restore_published_receipt('Owner/Repo')
    assert json.loads(Path('.monitor/published-ledger.json').read_text()) == {'change_ledger': 'a' * 64}


def test_receipt_api_outage_keeps_changes_unacknowledged(tmp_path, monkeypatch):
    script = module()
    monkeypatch.chdir(tmp_path)
    Path('.monitor').mkdir()
    receipt = Path('.monitor/published-ledger.json')
    receipt.write_text('{"change_ledger":"stale"}')
    def gh(*args):
        raise subprocess.CalledProcessError(1, ['gh', *args])
    monkeypatch.setattr(script, 'gh', gh)
    script.restore_published_receipt('Owner/Repo')
    assert not receipt.exists()
