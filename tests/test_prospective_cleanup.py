"""GitHub variable/Actions boundaries only; no real configuration or source writes."""
import copy
import io
import json
from datetime import timedelta
from urllib.error import HTTPError, URLError

import pytest

from mozes import prospective_authorization as auth, prospective_cleanup as cleanup
from prospective_claim_support import CODE, GitHub


@pytest.fixture
def pending(monkeypatch):
    server = GitHub()
    identifier = '20261015T115959Z.' + CODE + '.' + 'c' * 32
    server.add_run('101', identifier)
    current = server.runs['101']
    current['event'] = 'workflow_dispatch'
    claim, monitor = server.jobs['101']
    claim.update(status='completed', conclusion='skipped', steps=[])
    monitor.update(status='completed', conclusion='success')
    server.jobs['101'].append({'id': 1013, 'run_id': 101, 'run_attempt': 1,
        'name': cleanup.JOB, 'status': 'in_progress', 'conclusion': None})
    rows = {cleanup.CAPTURE: {'name': cleanup.CAPTURE, 'value': '1', 'updated_at': 'original'},
        cleanup.AUTHORIZATION: {'name': cleanup.AUTHORIZATION, 'value': identifier, 'updated_at': 'original'},
        cleanup.EVENT_IDS: {'name': cleanup.EVENT_IDS, 'value': json.dumps([auth.EVENT]), 'updated_at': 'original'}}
    writes = []
    def variable(name, *, method='GET', value=None):
        if method == 'GET': return copy.deepcopy(rows.get(name))
        writes.append((name, method, value))
        if method == 'PATCH': rows[name].update(value=value, updated_at='off')
        else: rows.pop(name, None)
    values = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': auth.REPO,
        'GITHUB_WORKFLOW': auth.WORKFLOW, 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_RUN_ID': '101',
        'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': CODE, 'GITHUB_JOB': 'prospective_cleanup',
        cleanup.AUTHORIZATION: identifier}
    for key, value in values.items(): monkeypatch.setenv(key, value)
    monkeypatch.setattr(auth, 'api', server.api)
    monkeypatch.setattr(cleanup, 'utcnow', lambda: server.now)
    monkeypatch.setattr(cleanup, 'variable', variable)
    return server, rows, writes, identifier


@pytest.fixture
def finished(pending):
    server, rows, writes, identifier = pending
    server.add_run('100', identifier)
    server.completed_claim('100')
    server.jobs['100'][1].update(status='completed', conclusion='success')
    server.runs['100']['status'] = 'completed'
    return pending


def assert_off(rows, writes):
    assert rows == {cleanup.CAPTURE: {'name': cleanup.CAPTURE, 'value': '0', 'updated_at': 'off'}}
    assert writes == [(cleanup.CAPTURE, 'PATCH', '0'), (cleanup.EVENT_IDS, 'DELETE', None),
                      (cleanup.AUTHORIZATION, 'DELETE', None)]


def test_pending_dispatch_cannot_end_unconsumed_authorization(pending):
    server, rows, writes, _ = pending
    before = copy.deepcopy(rows)
    assert cleanup.cleanup() == 'pending'
    assert rows == before and not writes
    assert all(body is None for _, body in server.calls)


@pytest.mark.parametrize('conclusion', ['success', 'failure', 'cancelled', 'timed_out'])
def test_completed_attempt_turns_off_even_after_failure_or_lost_local_operator(finished, conclusion):
    server, rows, writes, _ = finished
    server.runs['100']['conclusion'] = conclusion
    server.jobs['100'][1]['conclusion'] = conclusion
    assert cleanup.cleanup() == 'off_attempt_finished'
    assert_off(rows, writes)
    assert not server.refs and all(body is None for _, body in server.calls)


@pytest.mark.parametrize('phase', ['setup', 'claim', 'permit', 'checkpoint_upload'])
def test_no_ref_or_checkpoint_is_needed_to_close_failed_attempt(finished, phase):
    server, rows, writes, _ = finished
    job, monitor = server.jobs['100']
    if phase in ('setup', 'claim'):
        job['conclusion'] = 'failure'
        job['steps'][0]['conclusion'] = 'skipped' if phase == 'setup' else 'failure'
    else:
        monitor['conclusion'] = 'failure'
    assert cleanup.cleanup() == 'off_attempt_finished'
    assert_off(rows, writes)


def test_same_workflow_final_job_closes_attempt_before_next_serialized_run(pending, monkeypatch):
    server, rows, writes, identifier = pending
    server.runs['101']['event'] = 'schedule'
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'schedule')
    server.jobs['101'][0].update(status='completed', conclusion='success',
        steps=[{'name': auth.STEP + identifier, 'status': 'completed', 'conclusion': 'success'}])
    assert cleanup.cleanup() == 'off_attempt_finished'
    assert_off(rows, writes)
    # Duplicate cleanup / later queued run does not rewrite OFF or recreate an ID.
    writes.clear()
    assert cleanup.cleanup() == 'already_off' and not writes


def test_ongoing_monitor_is_not_cut_off_by_a_stale_completed_claim(finished):
    server, _, writes, _ = finished
    server.jobs['100'][1]['status'] = 'in_progress'
    assert cleanup.cleanup() == 'pending' and not writes


@pytest.mark.parametrize(('delta', 'expected'), [(timedelta(hours=12, microseconds=-1), 'pending'),
    (timedelta(hours=12), 'off_expired'), (timedelta(hours=12, seconds=1), 'off_expired')])
def test_exact_expiry_boundary_needs_no_capture_or_claim(pending, delta, expected):
    server, rows, writes, identifier = pending
    issued = auth.stamp('2026-10-15T11:59:59Z')
    server.now = issued + delta
    assert cleanup.cleanup() == expected
    if expected == 'pending': assert not writes
    else: assert_off(rows, writes)


@pytest.mark.parametrize('problem', ['new_authorization', 'other_event', 'invalid_id', 'future',
    'wrong_job', 'wrong_repo', 'PR', 'run_attempt', 'foreign_claim', 'missing_monitor', 'duplicate_job', 'truncated'])
def test_ambiguous_scope_or_ownership_never_mutates_configuration(finished, monkeypatch, problem):
    server, rows, writes, identifier = finished
    if problem == 'new_authorization': rows[cleanup.AUTHORIZATION]['value'] = identifier[:-1] + 'd'
    if problem == 'other_event': rows[cleanup.EVENT_IDS]['value'] = '["OTHER"]'
    if problem in ('invalid_id', 'future'):
        identifier = 'invalid' if problem == 'invalid_id' else identifier.replace('20261015', '20261016')
        rows[cleanup.AUTHORIZATION]['value'] = identifier
        monkeypatch.setenv(cleanup.AUTHORIZATION, identifier)
    if problem == 'wrong_job': monkeypatch.setenv('GITHUB_JOB', 'monitor')
    if problem == 'wrong_repo': monkeypatch.setenv('GITHUB_REPOSITORY', 'Other/Repo')
    if problem == 'PR': monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    if problem == 'run_attempt': monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    if problem == 'foreign_claim': server.jobs['100'][0]['run_id'] = 99
    if problem == 'missing_monitor': server.jobs['100'].pop()
    if problem == 'duplicate_job': server.jobs['101'].append(copy.deepcopy(server.jobs['101'][-1]))
    if problem == 'truncated':
        original = server.api
        def truncated(path, **kw):
            value = original(path, **kw)
            if 'total_count' in value: value['total_count'] += 1
            return value
        monkeypatch.setattr(auth, 'api', truncated)
    with pytest.raises((auth.AuthorizationError, ValueError)): cleanup.cleanup()
    assert not writes


def test_observed_operator_configuration_race_refuses_all_writes(finished, monkeypatch):
    _, rows, writes, _ = finished
    original = cleanup.snapshot
    count = 0
    def changed():
        nonlocal count
        count += 1
        if count == 2: rows[cleanup.AUTHORIZATION]['updated_at'] = 'operator-changed'
        return original()
    monkeypatch.setattr(cleanup, 'snapshot', changed)
    with pytest.raises(auth.AuthorizationError, match='configuration_changed'): cleanup.cleanup()
    assert not writes


@pytest.mark.parametrize('failed_call', [cleanup.CAPTURE, cleanup.EVENT_IDS, cleanup.AUTHORIZATION])
def test_ambiguous_partial_cleanup_is_OFF_only_and_resumable_without_new_claim(finished, monkeypatch, failed_call):
    server, rows, writes, _ = finished
    original = cleanup.variable
    def ambiguous(name, **kw):
        result = original(name, **kw)
        if name == failed_call and kw.get('method', 'GET') != 'GET':
            raise auth.AuthorizationError('ambiguous')
        return result
    monkeypatch.setattr(cleanup, 'variable', ambiguous)
    with pytest.raises(auth.AuthorizationError): cleanup.cleanup()
    assert rows[cleanup.CAPTURE]['value'] == '0'
    monkeypatch.setattr(cleanup, 'variable', original)
    assert cleanup.cleanup() in ('off_attempt_finished', 'already_off')
    assert rows[cleanup.CAPTURE]['value'] == '0'
    assert cleanup.AUTHORIZATION not in rows and cleanup.EVENT_IDS not in rows
    assert not server.refs and all(body is None for _, body in server.calls)


@pytest.mark.parametrize(('name', 'method', 'value'), [('OTHER', 'GET', None),
    (cleanup.CAPTURE, 'PATCH', '1'), (cleanup.AUTHORIZATION, 'PATCH', 'new'),
    (cleanup.EVENT_IDS, 'PATCH', '[]'), (cleanup.CAPTURE, 'DELETE', None)])
def test_api_has_no_activation_or_unrelated_variable_mutation_path(monkeypatch, name, method, value):
    monkeypatch.setattr(cleanup, 'urlopen', lambda *a, **k: pytest.fail('unexpected network'))
    with pytest.raises(auth.AuthorizationError, match='outside_scope'):
        cleanup.variable(name, method=method, value=value)


def test_real_api_adapter_requires_token_and_never_exposes_error_or_token(monkeypatch, capsys):
    monkeypatch.delenv('MOZES_PROSPECTIVE_CLEANUP_TOKEN', raising=False)
    with pytest.raises(auth.AuthorizationError, match='credential_missing'): cleanup.variable(cleanup.CAPTURE)
    monkeypatch.setenv('MOZES_PROSPECTIVE_CLEANUP_TOKEN', 'synthetic-private-token')
    def unavailable(*a, **kw): raise URLError('synthetic-private-token')
    monkeypatch.setattr(cleanup, 'urlopen', unavailable)
    with pytest.raises(auth.AuthorizationError) as caught: cleanup.variable(cleanup.CAPTURE)
    assert 'synthetic-private-token' not in str(caught.value) + capsys.readouterr().out


def test_api_accepts_only_documented_responses_and_missing_GET(monkeypatch):
    monkeypatch.setenv('MOZES_PROSPECTIVE_CLEANUP_TOKEN', 'synthetic-private-token')
    seen = []
    class Response(io.BytesIO):
        status = 204
    def opened(request, timeout):
        seen.append((request.full_url, request.get_method(), request.data, timeout))
        return Response()
    monkeypatch.setattr(cleanup, 'urlopen', opened)
    cleanup.variable(cleanup.CAPTURE, method='PATCH', value='0')
    assert seen == [(f'https://api.github.com/repos/{auth.REPO}/actions/variables/{cleanup.CAPTURE}',
                     'PATCH', b'{"value": "0"}', 10)]
    def missing(*a, **k): raise HTTPError('url', 404, 'missing', {}, None)
    monkeypatch.setattr(cleanup, 'urlopen', missing)
    assert cleanup.variable(cleanup.AUTHORIZATION) is None
    with pytest.raises(auth.AuthorizationError): cleanup.variable(cleanup.AUTHORIZATION, method='DELETE')


@pytest.mark.parametrize('status', [200, 201, 202, 302, 403, 429, 500])
def test_uncertain_variable_write_is_never_reported_as_confirmed_OFF(monkeypatch, status):
    monkeypatch.setenv('MOZES_PROSPECTIVE_CLEANUP_TOKEN', 'synthetic-token')
    class Response(io.BytesIO):
        pass
    def response(*args, **kwargs):
        item = Response()
        item.status = status
        return item
    monkeypatch.setattr(cleanup, 'urlopen', response)
    with pytest.raises(auth.AuthorizationError): cleanup.variable(cleanup.CAPTURE, method='PATCH', value='0')


def test_cleanup_never_receives_source_or_notification_credentials():
    from pathlib import Path
    text = (Path(__file__).parents[1] / '.github/workflows/lightweight-monitor.yml').read_text()
    block = text.split('  prospective_cleanup:', 1)[1]
    assert 'pip install' not in block and 'MOZES_DB_PATH' not in block
    assert all(name not in block for name in ('SEC_USER_AGENT', 'EDGE_SYNC_TOKEN', 'MOZES_WEBHOOK_URL',
                                            'MOZES_PROSPECTIVE_EVENT_IDS:'))
    assert text.count('MOZES_PROSPECTIVE_CLEANUP_TOKEN: ${{ secrets.MOZES_PROSPECTIVE_CLEANUP_TOKEN }}') == 1


def test_variable_token_cannot_follow_a_redirect_to_another_repository_or_host():
    assert cleanup.NoRedirect().redirect_request(None, None, 302, 'moved', {},
                                                 'https://other.example/variable') is None
