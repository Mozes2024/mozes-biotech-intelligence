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
    def gh(*args, **kwargs):
        if args[0] == 'api' and '/workflows/' in args[1]:
            return json.dumps({'total_count':2, 'workflow_runs':[
                {'id':i,'head_branch':'main','status':'completed','conclusion':'success',
                 'updated_at':f'2026-10-12T05:00:0{3-i}+00:00'} for i in (1,2)]})
        if args[0] == 'api' and '/jobs?' in args[1]:
            return json.dumps({'total_count':1,'jobs':[{'name':'deploy','conclusion':'success'}]})
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
    def gh(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ['gh', *args])
    monkeypatch.setattr(script, 'gh', gh)
    script.restore_published_receipt('Owner/Repo')
    assert not receipt.exists()


def test_receipt_order_follows_completion_and_skips_proven_non_deployment(tmp_path, monkeypatch):
    script=module();monkeypatch.chdir(tmp_path)
    # The old queued request completes last; an even later success skipped deployment.
    rows=[{'id':i,'status':'completed','conclusion':'success','head_branch':'main',
           'updated_at':f'2026-10-12T06:00:0{at}+00:00'} for i,at in [(3,1),(1,2),(4,3)]]
    downloads=[]
    def gh(*args,**kwargs):
        if args[0]=='api':
            path=args[1]
            if '/workflows/' in path:return json.dumps({'total_count':len(rows),'workflow_runs':rows})
            if '/jobs?' in path:
                return json.dumps({'total_count':1,'jobs':[{'name':'deploy','status':'completed',
                    'conclusion':'skipped' if '/4/' in path else 'success'}]})
            return json.dumps({'head_branch':'main','name':'deploy-pages','head_repository':{'full_name':'Owner/Repo'},'conclusion':'success'})
        downloads.append(args[2]);Path(args[-1],'publish-receipt.json').write_text(json.dumps({'change_ledger':'a'*64}))
        return ''
    monkeypatch.setattr(script,'gh',gh)
    assert script.restore_published_receipt('Owner/Repo')=={'change_ledger':'a'*64}
    assert downloads==['1']
