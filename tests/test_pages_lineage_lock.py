from copy import deepcopy
from pathlib import Path
import pytest
from test_pages_consumer import pages, fixture, mock_api


def test_publication_and_monitor_share_one_queue_through_deployment():
    root=Path(__file__).resolve().parents[1]
    for name in ('pages.yml','lightweight-monitor.yml'):
        text=(root/'.github/workflows'/name).read_text()
        assert 'group: mozes-hot-monitor' in text
        assert 'cancel-in-progress: false' in text
        assert 'queue: max' in text
    text=(root/'.github/workflows/pages.yml').read_text()
    assert 'MOZES_PAGES_LINEAGE_LOCK: mozes-hot-monitor' in text
    assert 'Verify source and publication ordering before deployment' in text
    assert 'Acknowledge ledger only after successful deployment' in text


@pytest.mark.parametrize('locked',[False,True])
def test_waiting_producer_does_not_starve_locked_consumer_but_ordinary_reader_blocks(tmp_path,monkeypatch,locked):
    _,_,_,run,artifact=fixture(tmp_path)
    waiting=deepcopy(run);waiting.update(id=3,status='pending',conclusion=None)
    mock_api(monkeypatch,run,artifact,extra=[waiting])
    original=pages.api
    monkeypatch.setattr(pages,'api',lambda path: {'total_count':0,'jobs':[]} if path.startswith('runs/3/') else original(path))
    if locked:
        monkeypatch.setenv('MOZES_PAGES_LINEAGE_LOCK','mozes-hot-monitor')
        monkeypatch.setenv('GITHUB_WORKFLOW','deploy-pages')
        monkeypatch.setenv('GITHUB_REF','refs/heads/main')
        assert pages.newest()[1]['id']==artifact['id']
    else:
        with pytest.raises(RuntimeError,match='producer_busy'):pages.newest()


@pytest.mark.parametrize('status,jobs',[
    ('in_progress',[]),
    ('pending',[{'name':'monitor','status':'in_progress','steps':[]}]),
    ('pending',[{'name':'monitor','status':'queued','started_at':'2026-10-20T12:00:00Z','steps':[]}]),
    ('pending',[{'name':'monitor','status':'completed','steps':[]}]),
])
def test_any_execution_remains_a_conflicting_producer_even_under_consumer_lock(tmp_path,monkeypatch,status,jobs):
    _,_,_,run,artifact=fixture(tmp_path)
    waiting=deepcopy(run);waiting.update(id=3,status=status,conclusion=None)
    mock_api(monkeypatch,run,artifact,extra=[waiting])
    original=pages.api
    monkeypatch.setattr(pages,'api',lambda path: {'total_count':len(jobs),'jobs':jobs} if path.startswith('runs/3/') else original(path))
    monkeypatch.setenv('MOZES_PAGES_LINEAGE_LOCK','mozes-hot-monitor')
    monkeypatch.setenv('GITHUB_WORKFLOW','deploy-pages')
    monkeypatch.setenv('GITHUB_REF','refs/heads/main')
    with pytest.raises(RuntimeError,match='producer_busy'):pages.newest()


@pytest.mark.parametrize('workflow,ref',[('ci','refs/heads/main'),('deploy-pages','refs/heads/other')])
def test_marker_alone_does_not_authorize_ignoring_waiters(tmp_path,monkeypatch,workflow,ref):
    _,_,_,run,artifact=fixture(tmp_path)
    waiting=deepcopy(run);waiting.update(id=3,status='pending',conclusion=None)
    mock_api(monkeypatch,run,artifact,extra=[waiting])
    original=pages.api
    monkeypatch.setattr(pages,'api',lambda path: {'total_count':0,'jobs':[]} if path.startswith('runs/3/') else original(path))
    monkeypatch.setenv('MOZES_PAGES_LINEAGE_LOCK','mozes-hot-monitor')
    monkeypatch.setenv('GITHUB_WORKFLOW',workflow)
    monkeypatch.setenv('GITHUB_REF',ref)
    with pytest.raises(RuntimeError,match='producer_busy'):pages.newest()
