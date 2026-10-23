"""Bounded automatic retries with a fake clock and synthetic inventories only."""
import importlib.util
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from test_pages_consumer import NOW, SHA, fixture


def mock_inventory(monkeypatch, pages, run, artifact, extra=()):
    def api(path):
        if path.startswith('artifacts?'):
            return {'total_count':1, 'artifacts':[artifact]}
        if path.startswith('workflows/'):
            return {'total_count':1 + len(extra), 'workflow_runs':[run, *extra]}
        return {'total_count':1, 'jobs':[{'name':'monitor', 'status':'completed', 'steps':[
            {'name':name, 'status':'completed', 'conclusion':'success'} for name in
            ('Bind durable checkpoint to monitor database', 'Run actions/upload-artifact@v4')]}]}
    monkeypatch.setattr(pages, 'api', api)


@pytest.fixture
def pages(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    spec = importlib.util.spec_from_file_location('pages_automatic',
        Path(__file__).resolve().parents[1] / 'scripts/restore_pages_state.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clock(pages, monkeypatch):
    state = {'now': 0.0, 'waits': []}
    monkeypatch.setattr(pages.time, 'monotonic', lambda: state['now'])
    def sleep(seconds):
        state['waits'].append(seconds)
        state['now'] += seconds
    monkeypatch.setattr(pages.time, 'sleep', sleep)
    return state


def environment(pages, monkeypatch, tmp_path, source):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_REPOSITORY', pages.REPO)
    monkeypatch.setenv('MOZES_DB_PATH', str(tmp_path / 'consumer.db'))
    def gh(*args, **kwargs):
        if args[0] == 'api': return json.dumps({'object': {'sha': SHA}})
        for path in source.iterdir(): shutil.copyfile(path, Path(args[-1]) / path.name)
        return ''
    monkeypatch.setattr(pages, 'gh', gh)


def test_running_producer_finishes_after_old_retry_window_with_fresh_inventory(pages, clock, monkeypatch, tmp_path):
    incoming = tmp_path / 'source'; incoming.mkdir()
    source, _, _, run, artifact = fixture(incoming)
    before = source.read_bytes()
    environment(pages, monkeypatch, tmp_path, incoming)
    mock_inventory(monkeypatch, pages, run, artifact)
    original_api = pages.api
    selections = []
    def api(path):
        result = original_api(path)
        if path.startswith('workflows/'):
            selections.append(clock['now'])
            if clock['now'] < 85:
                result['workflow_runs'] = [dict(run, status='in_progress')]
        return result
    monkeypatch.setattr(pages, 'api', api)
    # Reproduce current-main exhaustion using the same inventory and clock:
    # a producer busy for 85 seconds outlasts attempts at 0, 20 and 40.
    with pytest.raises(RuntimeError, match='producer_busy'):
        pages.restore(now=NOW)
    assert selections == [0, 20, 40]
    clock['now'] = 0
    clock['waits'].clear()
    selections.clear()
    receipt = pages.restore(now=NOW, automatic=True, expected_sha=SHA)
    assert selections[:4] == [0, 20, 50, 90]
    assert receipt['artifact_id'] == 7
    assert (tmp_path / 'consumer.db').read_bytes() == source.read_bytes() == before
    assert pages.RESTORE_DEADLINE is None


def test_multiple_queued_producers_and_continuous_arrivals_fail_closed(pages, clock, monkeypatch, tmp_path):
    incoming = tmp_path / 'source'; incoming.mkdir()
    source, _, _, run, artifact = fixture(incoming)
    before = source.read_bytes()
    environment(pages, monkeypatch, tmp_path, incoming)
    mock_inventory(monkeypatch, pages, run, artifact,
             extra=[dict(run, id=3, status='queued'), dict(run, id=4, status='in_progress')])
    ledger = tmp_path / '.monitor/published-ledger.json'; ledger.parent.mkdir()
    ledger.write_bytes(b'previous immutable acknowledgement')
    with pytest.raises(RuntimeError, match='producer_busy|restore_time_budget'):
        pages.restore(now=NOW, automatic=True, expected_sha=SHA)
    assert clock['now'] <= 300 and len(clock['waits']) < 8
    assert not (tmp_path / 'consumer.db').exists()
    assert ledger.read_bytes() == b'previous immutable acknowledgement'
    assert source.read_bytes() == before
    assert pages.RESTORE_DEADLINE is None


def test_reselection_discards_checkpoint_that_changed_after_wait(pages, clock, monkeypatch, tmp_path):
    first = tmp_path / 'first'; first.mkdir()
    second = tmp_path / 'second'; second.mkdir()
    a, _, _, ra, aa = fixture(first)
    b, checkpoint, _, rb, ab = fixture(second)
    with sqlite3.connect(b) as conn:
        conn.execute("INSERT INTO preserved VALUES('NEW','new checkpoint')")
    rb['id'] = 3; ab.update(id=8, workflow_run={'id':3})
    checkpoint['producer_run_id'] = '3'
    checkpoint['db_sha256'] = hashlib.sha256(b.read_bytes()).hexdigest()
    (second/'edge-checkpoint.json').write_text(json.dumps(checkpoint))
    environment(pages, monkeypatch, tmp_path, second)
    selections = iter([RuntimeError('producer_busy'), (ra, aa), (rb, ab), (rb, ab), (rb, ab)])
    def newest():
        selected = next(selections)
        if isinstance(selected, Exception): raise selected
        return selected
    monkeypatch.setattr(pages, 'newest', newest)
    downloads = []
    def gh(*args, **kwargs):
        if args[0] == 'api': return json.dumps({'object':{'sha':SHA}})
        directory = first if args[2] == '2' else second
        downloads.append(args[2])
        for path in directory.iterdir(): shutil.copyfile(path, Path(args[-1])/path.name)
        return ''
    monkeypatch.setattr(pages, 'gh', gh)
    assert pages.restore(now=NOW, automatic=True, expected_sha=SHA)['artifact_id'] == 8
    assert downloads == ['2', '3']
    assert (tmp_path/'consumer.db').read_bytes() == b.read_bytes() != a.read_bytes()


@pytest.mark.parametrize('phase', ['before', 'after_wait', 'after_restore'])
def test_newer_code_blocks_before_export(pages, clock, monkeypatch, tmp_path, phase):
    incoming = tmp_path/'source'; incoming.mkdir()
    fixture(incoming)
    environment(pages, monkeypatch, tmp_path, incoming)
    calls = []
    def check(expected):
        calls.append(expected)
        if phase == 'before' or len(calls) == 2:
            raise RuntimeError('publication_code_superseded')
    monkeypatch.setattr(pages, '_require_current_code', check)
    def attempt(**kwargs):
        if phase == 'after_wait': raise RuntimeError('producer_busy')
        return {'artifact_id':7}
    monkeypatch.setattr(pages, '_restore_once', attempt)
    with pytest.raises(RuntimeError, match='publication_code_superseded'):
        pages.restore(automatic=True, expected_sha=SHA)
    assert len(calls) <= 2


@pytest.mark.parametrize('category', ['incomplete_inventory', 'http_403', 'checkpoint_integrity',
                                    'artifact_provenance', 'publication_code_inventory'])
def test_untrusted_evidence_or_api_failure_never_retries(pages, clock, monkeypatch, category):
    monkeypatch.setattr(pages, '_require_current_code', lambda expected: None)
    def attempt(**kwargs): raise RuntimeError(category)
    monkeypatch.setattr(pages, '_restore_once', attempt)
    with pytest.raises(RuntimeError, match=category):
        pages.restore(automatic=True, expected_sha=SHA)
    assert clock['waits'] == []


def test_work_time_and_waits_share_one_deadline(pages, clock, monkeypatch):
    deadlines = []
    monkeypatch.setattr(pages, '_require_current_code', lambda expected: None)
    def attempt(**kwargs):
        deadlines.append(pages.RESTORE_DEADLINE)
        clock['now'] += 100
        raise RuntimeError('producer_busy')
    monkeypatch.setattr(pages, '_restore_once', attempt)
    with pytest.raises(RuntimeError, match='producer_busy|restore_time_budget'):
        pages.restore(automatic=True, expected_sha=SHA)
    assert set(deadlines) == {300}
    assert len(deadlines) <= 3 and pages.RESTORE_DEADLINE is None


def test_budget_expiry_during_validation_cannot_report_success(pages, clock, monkeypatch):
    monkeypatch.setattr(pages, '_require_current_code', lambda expected: None)
    def attempt(**kwargs):
        clock['now'] = 301
        return {'artifact_id':7}
    monkeypatch.setattr(pages, '_restore_once', attempt)
    with pytest.raises(RuntimeError, match='restore_time_budget'):
        pages.restore(automatic=True, expected_sha=SHA)


def test_automatic_policy_requires_a_pinned_sha_before_io(pages, clock, monkeypatch):
    monkeypatch.setattr(pages, 'gh', lambda *a, **k: pytest.fail('unpinned API call'))
    with pytest.raises(RuntimeError, match='expected_main_sha_required'):
        pages.restore(automatic=True)


@pytest.mark.parametrize('value', [{}, {'object':{}}, {'object':None}, {'object':{'sha':'unknown'}}, []])
def test_current_code_inventory_must_be_complete(pages, clock, monkeypatch, value):
    monkeypatch.setattr(pages, 'gh', lambda *a, **k: json.dumps(value))
    with pytest.raises(RuntimeError, match='publication_code_inventory'):
        pages.restore(automatic=True, expected_sha=SHA)
    assert clock['waits'] == []


def test_api_process_failure_has_no_wait_or_fallback(pages, clock, monkeypatch):
    def gh(*args, **kwargs): raise subprocess.CalledProcessError(1, 'gh')
    monkeypatch.setattr(pages, 'gh', gh)
    with pytest.raises(subprocess.CalledProcessError):
        pages.restore(automatic=True, expected_sha=SHA)
    assert clock['waits'] == [] and pages.RESTORE_DEADLINE is None


def test_subprocess_timeout_is_capped_by_shared_remaining_budget(pages, clock, monkeypatch):
    timeouts = []
    monkeypatch.setattr(pages.subprocess, 'check_output',
                        lambda *a, **k: timeouts.append(k['timeout']) or '{}')
    pages.RESTORE_DEADLINE = 10
    try:
        pages.gh('api', 'synthetic', timeout=30)
        clock['now'] = 10
        with pytest.raises(RuntimeError, match='restore_time_budget'):
            pages.gh('api', 'synthetic', timeout=30)
    finally:
        pages.RESTORE_DEADLINE = None
    assert timeouts == [10]


def test_code_moving_during_real_restore_never_logs_success(pages, clock, monkeypatch, tmp_path, capsys):
    incoming = tmp_path/'source'; incoming.mkdir()
    source, _, _, run, artifact = fixture(incoming)
    before = source.read_bytes()
    environment(pages, monkeypatch, tmp_path, incoming)
    mock_inventory(monkeypatch, pages, run, artifact)
    transport = pages.gh
    checks = []
    def gh(*args, **kwargs):
        if args[0] == 'api':
            checks.append(args)
            return json.dumps({'object':{'sha':SHA if len(checks) == 1 else 'b'*40}})
        return transport(*args, **kwargs)
    monkeypatch.setattr(pages, 'gh', gh)
    with pytest.raises(RuntimeError, match='publication_code_superseded'):
        pages.restore(now=NOW, automatic=True, expected_sha=SHA)
    assert 'pages_consumer_restore PASS' not in capsys.readouterr().out
    assert source.read_bytes() == before


def test_workflow_keeps_trigger_lock_preflight_and_ack_order():
    text = (Path(__file__).resolve().parents[1]/'.github/workflows/pages.yml').read_text()
    assert "workflows: ['ci']" in text
    assert '--automatic --expected-sha' in text
    assert 'group: mozes-hot-monitor' in text and 'cancel-in-progress: false' in text
    assert text.index('publication_control preflight') < text.index('uses: actions/deploy-pages@v4')
    assert text.index('uses: actions/deploy-pages@v4') < text.index('Acknowledge ledger only after successful deployment')


def test_monitor_dispatched_pages_uses_pinned_bounded_policy_too():
    text = (Path(__file__).resolve().parents[1]/'.github/workflows/pages.yml').read_text()
    step = text.split('- name: Restore known data without a new source scan', 1)[1].split('- name:', 1)[0]
    assert "GITHUB_EVENT_NAME" not in step
    assert "python -m scripts.restore_pages_state --automatic --expected-sha '${{ steps.guard.outputs.code_sha }}'" in step


def test_real_failure_timing_outlasts_dispatch_policy_but_bounded_policy_reselects(pages, clock, monkeypatch, tmp_path):
    # Both incident runs exhausted three busy attempts; the last producer ended
    # twelve seconds after the second Pages run gave up. Include inventory work.
    incoming = tmp_path/'source'; incoming.mkdir()
    source, _, _, run, artifact = fixture(incoming)
    before = source.read_bytes()
    environment(pages, monkeypatch, tmp_path, incoming)
    mock_inventory(monkeypatch, pages, run, artifact)
    original_api = pages.api
    def api(path):
        result = original_api(path)
        if path.startswith('workflows/'):
            clock['now'] += 27
            if clock['now'] < 133:
                result['workflow_runs'] = [dict(run, status='in_progress')]
        return result
    monkeypatch.setattr(pages, 'api', api)
    with pytest.raises(RuntimeError, match='producer_busy'):
        pages.restore(now=NOW)
    assert clock['now'] == 121
    assert not (tmp_path/'consumer.db').exists()
    clock['now'] = 0
    clock['waits'].clear()
    receipt = pages.restore(now=NOW, automatic=True, expected_sha=SHA)
    assert receipt['artifact_id'] == artifact['id']
    assert clock['waits'] == [20, 30, 40]
    assert clock['now'] < 300
    assert source.read_bytes() == (tmp_path/'consumer.db').read_bytes() == before


def _flaky_download_gh(pages, monkeypatch, tmp_path, source, failures):
    state = {'downloads': 0}
    def gh(*args, **kwargs):
        if args[0] == 'api':
            return json.dumps({'object': {'sha': SHA}})
        state['downloads'] += 1
        if state['downloads'] <= failures:
            (Path(args[-1]) / 'partial').write_text('junk')
            raise subprocess.CalledProcessError(1, ['gh'])
        for path in source.iterdir():
            shutil.copyfile(path, Path(args[-1]) / path.name)
        return ''
    monkeypatch.setattr(pages, 'gh', gh)
    return state


def test_transient_download_failure_retries_and_recovers(pages, clock, monkeypatch, tmp_path):
    incoming = tmp_path / 'source'; incoming.mkdir()
    source, _, _, run, artifact = fixture(incoming)
    mock_inventory(monkeypatch, pages, run, artifact)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_REPOSITORY', pages.REPO)
    monkeypatch.setenv('MOZES_DB_PATH', str(tmp_path / 'consumer.db'))
    state = _flaky_download_gh(pages, monkeypatch, tmp_path, incoming, failures=1)
    result = pages.restore(automatic=True, expected_sha=SHA)
    assert result['schema'] == 1
    assert state['downloads'] == 2


def test_persistent_download_failure_fails_closed_after_bounded_attempts(pages, clock, monkeypatch, tmp_path):
    incoming = tmp_path / 'source'; incoming.mkdir()
    source, _, _, run, artifact = fixture(incoming)
    mock_inventory(monkeypatch, pages, run, artifact)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_REPOSITORY', pages.REPO)
    monkeypatch.setenv('MOZES_DB_PATH', str(tmp_path / 'consumer.db'))
    state = _flaky_download_gh(pages, monkeypatch, tmp_path, incoming, failures=100)
    with pytest.raises(RuntimeError, match='artifact_download_failed'):
        pages.restore(automatic=True, expected_sha=SHA)
    assert state['downloads'] == pages.AUTOMATIC_RESTORE_ATTEMPTS
