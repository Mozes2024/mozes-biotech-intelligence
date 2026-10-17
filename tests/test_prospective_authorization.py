"""Offline claim/ref/Actions trust-boundary regressions; no real GitHub writes."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from mozes import prospective_authorization as auth
from prospective_claim_support import CODE, GitHub, issue_permit
from test_prospective_pilot import owner, prepared


@pytest.fixture
def candidate(monkeypatch, tmp_path):
    server = GitHub()
    identifier = '20261015T115959Z.' + CODE + '.' + 'c' * 32
    values = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': auth.REPO,
        'GITHUB_WORKFLOW': auth.WORKFLOW, 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_RUN_ID': '100', 'GITHUB_RUN_ATTEMPT': '1',
        'GITHUB_SHA': CODE, 'GITHUB_JOB': 'prospective_claim', 'RUNNER_TEMP': str(tmp_path),
        'MOZES_PROSPECTIVE_CAPTURE': '1', 'MOZES_PROSPECTIVE_CLEANUP_READY': 'true',
        'MOZES_PROSPECTIVE_EVENT_IDS': json.dumps([auth.EVENT]),
        'MOZES_PROSPECTIVE_AUTHORIZATION': identifier}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    for key in ('SOURCE_RUN_ID', 'INCIDENT_MODE', 'MOZES_PROSPECTIVE_PREPARE', 'MOZES_PROSPECTIVE_CLAIM_SHA'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(auth, 'api', server.api)
    monkeypatch.setattr(auth, 'utcnow', lambda: server.now)
    server.add_run('100', identifier)
    return server, identifier


def successor(server, identifier, monkeypatch, tmp_path):
    server.completed_claim('100', success=False)
    server.runs['100']['status'] = 'completed'
    server.add_run('101', identifier)
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    new = tmp_path / 'successor'
    new.mkdir()
    monkeypatch.setenv('RUNNER_TEMP', str(new))


def test_claim_binds_exact_owner_scope_and_never_updates_incident_ref(candidate):
    server, identifier = candidate
    sha = auth.claim()
    assert len(server.refs) == 1
    ref = server.refs['tags/prospective-pilot-' + identifier]
    assert ref['object']['sha'] == sha
    record = json.loads(server.commits[sha]['message'])
    assert record == {'schema': 1, 'authorization_id': identifier, 'event_id': auth.EVENT,
        'scope': auth.SCOPE, 'run_id': '100', 'run_attempt': 1, 'code_sha': CODE,
        'repository': auth.REPO, 'workflow': auth.PATH, 'claim_job_id': 1001}
    assert server.commits[sha]['parents'] == [{'sha': CODE}]
    assert server.commits[sha]['tree'] == server.commits[CODE]['tree']
    writes = [(p, body) for p, body in server.calls if body is not None]
    assert [p for p, _ in writes] == ['git/commits', 'git/refs']
    assert 'monitor-recovery' not in json.dumps(writes)


def test_claim_requires_github_cleanup_credential_before_any_git_write(candidate, monkeypatch):
    server, _ = candidate
    monkeypatch.delenv('MOZES_PROSPECTIVE_CLEANUP_READY', raising=False)
    with pytest.raises(auth.AuthorizationError, match='cleanup_not_configured'):
        auth.claim()
    assert not server.calls and not server.refs


def test_workflow_cleanup_runs_after_monitor_even_if_it_fails_without_local_supervision():
    text = (Path(__file__).parents[1] / '.github/workflows/lightweight-monitor.yml').read_text()
    assert '  prospective_cleanup:' in text
    cleanup = text.split('  prospective_cleanup:', 1)[1]
    assert 'always()' in cleanup and 'needs: [incident_eligibility, prospective_claim, monitor]' in cleanup
    assert 'python -m mozes.prospective_cleanup' in cleanup
    assert 'MOZES_PROSPECTIVE_CLEANUP_TOKEN: ${{ secrets.MOZES_PROSPECTIVE_CLEANUP_TOKEN }}' in cleanup
    assert 'contents: read' in cleanup and 'actions: read' in cleanup
    assert 'contents: write' not in cleanup and 'actions: write' not in cleanup
    assert 'schedule:' not in cleanup and 'workflow_dispatch:' not in cleanup


@pytest.mark.parametrize(('key', 'value'), [
    ('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_RUN_ATTEMPT', ''), ('GITHUB_EVENT_NAME', 'workflow_dispatch'),
    ('GITHUB_JOB', 'monitor'), ('GITHUB_SHA', 'd' * 40), ('GITHUB_REPOSITORY', 'Other/Repo'),
    ('GITHUB_WORKFLOW', 'other'), ('GITHUB_REF', 'refs/heads/branch'), ('GITHUB_ACTIONS', 'false'),
    ('GITHUB_RUN_ID', '0'), ('INCIDENT_MODE', 'recover'), ('SOURCE_RUN_ID', '99'),
    ('MOZES_PROSPECTIVE_PREPARE', '{}'), ('MOZES_PROSPECTIVE_CAPTURE', '0'),
    ('MOZES_PROSPECTIVE_EVENT_IDS', '["other"]'),
    ('MOZES_PROSPECTIVE_EVENT_IDS', json.dumps([auth.EVENT, auth.EVENT])),
    ('MOZES_PROSPECTIVE_AUTHORIZATION', ''), ('MOZES_PROSPECTIVE_AUTHORIZATION', 'old-authorization')])
def test_unapproved_identity_never_reaches_git_writes(candidate, monkeypatch, key, value):
    server, _ = candidate
    monkeypatch.setenv(key, value)
    with pytest.raises(auth.AuthorizationError):
        auth.claim()
    assert not [body for _, body in server.calls if body is not None]


@pytest.mark.parametrize('delta', [timedelta(hours=-1), timedelta(hours=12), timedelta(days=4)])
def test_same_authorization_cannot_be_extended_or_reused_after_history_expiry(candidate, delta):
    server, _ = candidate
    server.now += delta
    server.runs.clear()  # Even unavailable old Actions history cannot revive an expired ID.
    with pytest.raises(auth.AuthorizationError, match='expired_or_future'):
        auth.claim()
    assert not server.calls


@pytest.mark.parametrize('elapsed', [timedelta(0), timedelta(hours=1),
    timedelta(hours=11, minutes=59, seconds=59, microseconds=999999)])
def test_twelve_hour_window_accepts_issue_time_and_delayed_first_attempt(candidate, monkeypatch, elapsed):
    server, _ = candidate
    record, issued = auth.identity('prospective_claim')
    server.now = issued + elapsed
    server.runs['100']['created_at'] = server.now.isoformat()
    sha = auth.claim()
    server.completed_claim('100')
    monkeypatch.setenv('GITHUB_JOB', 'monitor')
    monkeypatch.setenv('MOZES_PROSPECTIVE_CLAIM_SHA', sha)
    auth.permit()
    assert auth.consume()['record']['authorization_id'] == record['authorization_id']
    with pytest.raises(auth.AuthorizationError, match='already_used'):
        auth.consume()
    assert len(server.refs) == 1


@pytest.mark.parametrize('elapsed', [timedelta(microseconds=-1), timedelta(hours=12),
    timedelta(hours=12, microseconds=1)])
def test_twelve_hour_window_rejects_before_issue_and_at_or_after_expiry(candidate, elapsed):
    server, _ = candidate
    _, issued = auth.identity('prospective_claim')
    server.now = issued + elapsed
    with pytest.raises(auth.AuthorizationError, match='expired_or_future'):
        auth.claim()
    assert not server.calls and not server.refs


@pytest.mark.parametrize('phase', ['permit', 'consume'])
def test_expiry_after_claim_never_grants_or_consumes_a_permit(candidate, monkeypatch, phase):
    server, _ = candidate
    _, issued = auth.identity('prospective_claim')
    server.now = issued + timedelta(hours=12, seconds=-1)
    server.runs['100']['created_at'] = server.now.isoformat()
    sha = auth.claim()
    server.completed_claim('100')
    monkeypatch.setenv('GITHUB_JOB', 'monitor')
    monkeypatch.setenv('MOZES_PROSPECTIVE_CLAIM_SHA', sha)
    if phase == 'consume':
        auth.permit()
    before = list(server.calls)
    server.now = issued + timedelta(hours=12)
    with pytest.raises(auth.AuthorizationError, match='expired_or_future'):
        getattr(auth, phase)()
    assert server.calls == before and len(server.refs) == 1


def test_early_ambiguous_attempt_is_still_consumed_near_twelve_hour_expiry(candidate, monkeypatch, tmp_path):
    server, identifier = candidate
    _, issued = auth.identity('prospective_claim')
    server.fail = ('before', 'git/refs')
    with pytest.raises(auth.AuthorizationError, match='ambiguous'):
        auth.claim()
    assert not server.refs
    server.fail = None
    successor(server, identifier, monkeypatch, tmp_path)
    server.now = issued + timedelta(hours=11, minutes=59)
    server.runs['101']['created_at'] = server.now.isoformat()
    with pytest.raises(auth.AuthorizationError, match='prior_claim_attempted'):
        auth.claim()
    from urllib.parse import parse_qs, urlsplit
    queries = [parse_qs(urlsplit(p).query) for p, _ in server.calls if p.startswith('actions/workflows/')]
    assert all(q['created'] == [issued.isoformat() + '..' + (issued + timedelta(hours=12)).isoformat()]
               for q in queries)
    assert len([p for p, body in server.calls if p == 'git/refs' and body]) == 1


@pytest.mark.parametrize('count', [52, 100])
def test_complete_twelve_hour_history_remains_bounded_and_checks_every_run(candidate, count):
    server, identifier = candidate
    _, issued = auth.identity('prospective_claim')
    server.now = issued + timedelta(hours=11, minutes=59)
    server.runs['100']['created_at'] = server.now.isoformat()
    for run_id in range(1, count):
        server.add_run(str(run_id), identifier)
        server.runs[str(run_id)]['status'] = 'completed'
        for job in server.jobs[str(run_id)]:
            job.update(status='completed', conclusion='skipped', steps=[])
    assert auth.claim()
    jobs = [p for p, _ in server.calls if '/jobs?' in p]
    assert len(jobs) == count and len(set(jobs)) == count
    assert len(server.calls) <= 110


@pytest.mark.parametrize('problem', ['truncated', 'over_bound', 'duplicate', 'missing_current'])
def test_twelve_hour_history_never_truncates_or_guesses_at_inventory(candidate, monkeypatch, problem):
    server, _ = candidate
    _, issued = auth.identity('prospective_claim')
    server.now = issued + timedelta(hours=11)
    original = server.api
    def incomplete(path, **kwargs):
        value = original(path, **kwargs)
        if path.startswith('actions/workflows/'):
            rows = [{**server.runs['100'], 'id': n} for n in range(1, 102)]
            if problem == 'truncated': return {'total_count': 101, 'workflow_runs': rows[:100]}
            if problem == 'over_bound': return {'total_count': 101, 'workflow_runs': rows}
            if problem == 'duplicate': return {'total_count': 2, 'workflow_runs': [rows[99], rows[99]]}
            return {'total_count': 99, 'workflow_runs': rows[:99]}
        return value
    monkeypatch.setattr(auth, 'api', incomplete)
    reason = 'current_run_missing' if problem == 'missing_current' else 'inventory_incomplete'
    with pytest.raises(auth.AuthorizationError, match=reason):
        auth.claim()
    assert not server.refs and not [body for _, body in server.calls if body is not None]


@pytest.mark.parametrize('problem', ['truncated', 'over_bound', 'duplicate'])
def test_prior_job_inventory_is_complete_and_bounded_across_twelve_hours(candidate, monkeypatch, problem):
    server, identifier = candidate
    _, issued = auth.identity('prospective_claim')
    server.now = issued + timedelta(hours=11)
    server.add_run('99', identifier)
    server.runs['99']['status'] = 'completed'
    original = server.api
    def incomplete(path, **kwargs):
        value = original(path, **kwargs)
        if path.startswith('actions/runs/99/jobs?'):
            if problem == 'truncated': value['total_count'] += 1
            elif problem == 'duplicate': value['jobs'] = [value['jobs'][0]] * 2
            else:
                value = {'total_count': 101, 'jobs': [{**value['jobs'][0], 'id': n} for n in range(1, 102)]}
        return value
    monkeypatch.setattr(auth, 'api', incomplete)
    with pytest.raises(auth.AuthorizationError, match='inventory_incomplete'):
        auth.claim()
    assert not server.refs and not [body for _, body in server.calls if body is not None]


@pytest.mark.parametrize('field', ['head_sha', 'head_branch', 'run_attempt', 'event', 'path', 'created_at'])
def test_remote_run_must_match_local_owner(candidate, field):
    server, _ = candidate
    server.runs['100'][field] = 2 if field == 'run_attempt' else 'wrong'
    with pytest.raises(auth.AuthorizationError):
        auth.claim()
    assert not server.refs


def test_main_advance_blocks_claim(candidate, monkeypatch):
    server, _ = candidate
    original = server.api
    monkeypatch.setattr(auth, 'api', lambda p, **kw: {'sha': 'd' * 40} if p == 'commits/main' else original(p, **kw))
    with pytest.raises(auth.AuthorizationError, match='main_changed'):
        auth.claim()
    assert not server.refs


@pytest.mark.parametrize('fault', ['before', 'after'])
def test_ambiguous_ref_creation_burns_authorization_even_without_a_ref(candidate, monkeypatch, tmp_path, fault):
    server, identifier = candidate
    server.fail = (fault, 'git/refs')
    with pytest.raises(auth.AuthorizationError, match='ambiguous'):
        auth.claim()
    assert len(server.refs) == (fault == 'after')
    # A process restart in this job cannot retry even when no ref was created.
    server.fail = None
    with pytest.raises(auth.AuthorizationError, match='already_used'):
        auth.claim()
    successor(server, identifier, monkeypatch, tmp_path)
    with pytest.raises(auth.AuthorizationError, match='prior_claim_attempted'):
        auth.claim()
    assert len([p for p, body in server.calls if p == 'git/refs' and body]) == 1


@pytest.mark.parametrize('delay', [timedelta(0), timedelta(hours=11)])
def test_confirmed_claim_is_never_adopted_by_queued_successor(candidate, monkeypatch, tmp_path, delay):
    server, identifier = candidate
    auth.claim()
    successor(server, identifier, monkeypatch, tmp_path)
    server.now += delay
    server.runs['101']['created_at'] = server.now.isoformat()
    with pytest.raises(auth.AuthorizationError):
        auth.claim()
    assert len(server.refs) == 1


def test_already_queued_successor_does_not_prevent_first_claim_or_gain_ownership(candidate, monkeypatch, tmp_path):
    server, identifier = candidate
    server.add_run('101', identifier, status='pending')
    assert auth.claim()
    server.completed_claim('100')
    server.add_run('101', identifier)
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    replacement = tmp_path / 'queued-successor'
    replacement.mkdir()
    monkeypatch.setenv('RUNNER_TEMP', str(replacement))
    with pytest.raises(auth.AuthorizationError, match='prior_claim_attempted'):
        auth.claim()
    assert len(server.refs) == 1


def test_earlier_queued_claim_is_not_inferred_unattempted(candidate):
    server, identifier = candidate
    server.add_run('99', identifier, status='pending')
    with pytest.raises(auth.AuthorizationError, match='earlier_claim_uncertain'):
        auth.claim()
    assert not server.refs


def test_existing_ref_cannot_be_adopted_even_by_original_owner(candidate, monkeypatch, tmp_path):
    server, _ = candidate
    auth.claim()
    # Simulated replacement runner cannot recreate the confirmed permanent claim.
    other = tmp_path / 'replacement'
    other.mkdir()
    monkeypatch.setenv('RUNNER_TEMP', str(other))
    with pytest.raises(auth.AuthorizationError, match='claim_exists'):
        auth.claim()
    assert len(server.refs) == 1


def test_atomic_ref_conflict_never_grants_a_second_winner(candidate, monkeypatch):
    server, identifier = candidate
    original = server.api
    def race(path, **kwargs):
        if path == 'git/refs':
            server.refs['tags/prospective-pilot-' + identifier] = {
                'ref': 'refs/tags/prospective-pilot-' + identifier,
                'object': {'sha': 'd' * 40, 'type': 'commit'}}
        return original(path, **kwargs)
    monkeypatch.setattr(auth, 'api', race)
    with pytest.raises(auth.AuthorizationError, match='conflict'):
        auth.claim()
    assert next(iter(server.refs.values()))['object']['sha'] == 'd' * 40


@pytest.mark.parametrize('problem', ['missing_current', 'truncated', 'duplicate', 'unknown_prior'])
def test_incomplete_or_uncertain_actions_inventory_never_authorizes(candidate, monkeypatch, problem):
    server, _ = candidate
    original = server.api
    def incomplete(path, **kwargs):
        value = original(path, **kwargs)
        if path.startswith('actions/workflows/'):
            if problem == 'missing_current':
                return {'total_count': 0, 'workflow_runs': []}
            if problem == 'truncated':
                value['total_count'] += 1
            if problem == 'duplicate':
                value['workflow_runs'] *= 2
                value['total_count'] = 2
            if problem == 'unknown_prior':
                value['workflow_runs'].append({**server.runs['100'], 'id': 99})
                value['total_count'] += 1
        if problem == 'unknown_prior' and 'actions/runs/99/jobs' in path:
            return {'total_count': 0, 'jobs': []}
        return value
    # Avoid synthesizing an API response from a missing fake route.
    def route(path, **kwargs):
        if problem == 'unknown_prior' and 'actions/runs/99/jobs' in path:
            return {'total_count': 0, 'jobs': []}
        return incomplete(path, **kwargs)
    monkeypatch.setattr(auth, 'api', route)
    with pytest.raises(auth.AuthorizationError):
        auth.claim()
    assert not server.refs


def test_read_only_permit_is_single_use_across_process_reentry(monkeypatch, tmp_path):
    server = issue_permit(monkeypatch, tmp_path)
    before = len([body for _, body in server.calls if body])
    receipt = auth.consume()
    assert receipt['record']['event_id'] == auth.EVENT
    with pytest.raises(auth.AuthorizationError, match='already_used'):
        auth.consume()
    with pytest.raises(auth.AuthorizationError, match='already_used'):
        auth.permit()
    assert len([body for _, body in server.calls if body]) == before


@pytest.mark.parametrize('changed', ['attempt', 'run', 'event', 'claim_sha', 'proof'])
def test_replayed_or_mismatched_permit_cannot_enter_capture(monkeypatch, tmp_path, changed):
    issue_permit(monkeypatch, tmp_path)
    if changed == 'attempt': monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    if changed == 'run': monkeypatch.setenv('GITHUB_RUN_ID', '101')
    if changed == 'event': monkeypatch.setenv('MOZES_PROSPECTIVE_EVENT_IDS', '["other"]')
    if changed == 'claim_sha': monkeypatch.setenv('MOZES_PROSPECTIVE_CLAIM_SHA', 'd' * 40)
    if changed == 'proof':
        record, _ = auth.identity('monitor')
        path = auth.local_path(record, 'permit')
        value = json.loads(path.read_text())
        value['record']['scope'] = 'unbounded'
        path.write_text(json.dumps(value))
    with pytest.raises(auth.AuthorizationError):
        auth.consume()


def test_job_rerun_rejects_old_claim_outputs_before_reading_them(monkeypatch, tmp_path):
    server = issue_permit(monkeypatch, tmp_path)
    calls = len(server.calls)
    replacement = tmp_path / 'rerun'
    replacement.mkdir()
    monkeypatch.setenv('RUNNER_TEMP', str(replacement))
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    with pytest.raises(auth.AuthorizationError, match='attempt_mismatch'):
        auth.permit()
    with pytest.raises(auth.AuthorizationError, match='attempt_mismatch'):
        auth.consume()
    assert len(server.calls) == calls


@pytest.mark.parametrize('bad', ['ref', 'owner', 'parent', 'tree', 'claim_job', 'monitor_job'])
def test_permit_rechecks_remote_claim_and_both_job_owners(candidate, monkeypatch, tmp_path, bad):
    server, _ = candidate
    sha = auth.claim()
    server.completed_claim('100')
    monkeypatch.setenv('GITHUB_JOB', 'monitor')
    monkeypatch.setenv('MOZES_PROSPECTIVE_CLAIM_SHA', sha)
    if bad == 'ref': next(iter(server.refs.values()))['object']['sha'] = 'd' * 40
    if bad == 'owner':
        body = json.loads(server.commits[sha]['message']); body['run_id'] = '101'
        server.commits[sha]['message'] = json.dumps(body)
    if bad == 'parent': server.commits[sha]['parents'] = [{'sha': 'd' * 40}]
    if bad == 'tree': server.commits[sha]['tree']['sha'] = 'd' * 40
    if bad == 'claim_job': server.jobs['100'][0]['run_attempt'] = 2
    if bad == 'monitor_job': server.jobs['100'][1]['run_attempt'] = 2
    with pytest.raises(auth.AuthorizationError):
        auth.permit()
    assert not list(tmp_path.glob('*-permit'))


def test_token_errors_do_not_expose_api_stderr_or_retry(monkeypatch):
    calls = []
    class Result:
        returncode = 1
        stderr = 'synthetic-private-value (HTTP 403)'
    def failed(*args, **kwargs):
        calls.append((args, kwargs))
        return Result()
    monkeypatch.setattr(auth.subprocess, 'run', failed)
    with pytest.raises(auth.AuthorizationError) as error:
        auth.api('git/refs', body={'ref': 'synthetic'})
    assert 'synthetic-private-value' not in str(error.value) and len(calls) == 1


@pytest.mark.parametrize('status', ['201', '200', '202', '302'])
def test_git_write_transport_requires_confirmed_creation_not_adoption(monkeypatch, status):
    class Result:
        returncode = 0
        stdout = 'HTTP/2.0 ' + status + ' Result\nContent-Type: application/json\n\n{"sha":"synthetic"}'
    calls = []
    def response(*args, **kwargs):
        calls.append(args[0])
        return Result()
    monkeypatch.setattr(auth.subprocess, 'run', response)
    if status == '201':
        assert auth.api('git/commits', body={'tree': 'synthetic'}) == {'sha': 'synthetic'}
    else:
        with pytest.raises(auth.AuthorizationError):
            auth.api('git/commits', body={'tree': 'synthetic'})
    assert len(calls) == 1 and '--include' in calls[0]


def test_workflow_isolates_git_writes_and_never_claims_from_PR_or_dispatch():
    text = (Path(__file__).parents[1] / '.github/workflows/lightweight-monitor.yml').read_text()
    gate = text.split('  prospective_claim:', 1)[1].split('  monitor:', 1)[0]
    monitor = text.split('  monitor:', 1)[1]
    assert "github.event_name == 'schedule'" in gate
    assert 'contents: write' in gate and 'actions: read' in gate
    assert 'continue-on-error: true' in gate.split('    if:', 1)[0]
    assert 'contents: read' in monitor and 'contents: write' not in monitor
    assert 'persist-credentials: false' in gate
    assert "steps.claim.outcome == 'success'" in gate
    assert 'prospective_authorization claim' in gate and 'MOZES_DB_PATH' not in gate
    assert 'EDGE_SYNC_TOKEN' not in gate and 'SEC_USER_AGENT' not in gate
    assert text.count('prospective_authorization claim') == 1
    assert monitor.index('prospective_authorization permit') < monitor.index('id: restore')
    assert 'MOZES_PROSPECTIVE_CLAIM_SHA: ${{ needs.prospective_claim.outputs.claim_sha }}' in monitor
    assert "steps.pilot_permit.outcome == 'success' && needs.prospective_claim.outputs.claim_sha || ''" in monitor


def test_failed_checkpoint_upload_and_stale_restore_cannot_reauthorize(owner, monkeypatch, tmp_path):
    import sqlite3
    from mozes import prospective_pilot as pilot
    pilot, conn, _ = prepared(owner, monkeypatch)
    server = monkeypatch._prospective_github
    record, _ = auth.identity('monitor')
    stale = tmp_path / 'older-checkpoint.db'
    with sqlite3.connect(stale) as destination:
        conn.backup(destination)
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 1
    # Upload fails: another DB is restored, but the Git latch and consumed runner
    # marker are not part of SQLite and do not rewind with it.
    restored = sqlite3.connect(stale)
    restored.row_factory = sqlite3.Row
    archives = list(restored.execute('SELECT * FROM source_archive'))
    assert pilot.after_monitor(restored, monitor_status='OK')['captured'] == 0
    assert restored.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 0
    successor(server, record['authorization_id'], monkeypatch, tmp_path)
    monkeypatch.setenv('GITHUB_JOB', 'prospective_claim')
    with pytest.raises(auth.AuthorizationError):
        auth.claim()
    monkeypatch.setenv('GITHUB_JOB', 'monitor')
    assert pilot.after_monitor(restored, monitor_status='OK')['captured'] == 0
    assert list(restored.execute('SELECT * FROM source_archive')) == archives
    assert restored.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 0
    assert len(server.refs) == 1
    restored.close()


def test_failed_monitor_or_open_transaction_consumes_attempt_before_DB_work(owner, monkeypatch):
    pilot, conn, _ = prepared(owner, monkeypatch)
    assert pilot.after_monitor(conn, monitor_status='FAILED')['status'] == 'SKIPPED'
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 0
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 0


def test_consume_is_local_and_bounded_after_network_verification(monkeypatch, tmp_path):
    issue_permit(monkeypatch, tmp_path)
    def unavailable(*args, **kwargs):
        raise AssertionError('observer must not use the network')
    monkeypatch.setattr(auth, 'api', unavailable)
    assert auth.consume()['record']['run_attempt'] == 1


def test_failed_permit_step_cannot_use_a_partially_published_file(monkeypatch, tmp_path):
    issue_permit(monkeypatch, tmp_path)
    # Emulate the workflow's explicit step-outcome gate after a late permit-step
    # failure: even a complete-looking local receipt must not authorize capture.
    monkeypatch.setenv('MOZES_PROSPECTIVE_CLAIM_SHA', '')
    with pytest.raises(auth.AuthorizationError):
        auth.consume()


def test_two_real_pipeline_runs_with_global_flag_on_only_first_enters_capture(owner, monkeypatch, tmp_path):
    from mozes import db, edge_enrichment, pipeline_v2c as pipeline
    from mozes.intelligence_store import state_get
    pilot, conn, event = prepared(owner, monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, 'DB_PATH', owner[0])
    monkeypatch.setattr(pipeline, 'trim_cache', lambda *a: None)
    monkeypatch.setattr(pipeline, 'publication_due', lambda *a, **k: False)
    monkeypatch.setattr(pipeline, 'run_priority_pipeline', lambda c: {'status': 'OK', 'details': {}})
    before_alerts = list(conn.execute('SELECT * FROM alert_outbox'))
    before_gates = list(conn.execute('SELECT * FROM model_validation'))
    assert pipeline.main(['monitor', '--priority-only', '--output', str(tmp_path / 'first.json')]) == 0
    first = state_get(conn, pilot.HEALTH)
    assert first['status'] == 'OK' and first['captured'] == 1
    assert first['source_gaps'] == first['errors'] == 0
    archives = list(conn.execute('SELECT * FROM source_archive'))
    # Synthetic queued successor has the same globally enabled flag/old outputs.
    # Even a genuine new source revision must not enter capture under that claim.
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    db.add_event_source(conn, event['id'], 'https://example.org/queued-revision', 'company_ir',
        url='https://example.org/queued-revision', statement='A revised issuer forecast',
        published_at='2026-10-15T11:00:00Z', retrieved_at='2026-10-15T11:01:00Z')
    assert pipeline.main(['monitor', '--priority-only', '--output', str(tmp_path / 'second.json')]) == 0
    second = state_get(conn, pilot.HEALTH)
    assert second['status'] == 'SKIPPED' and second['captured'] == 0
    assert pilot.verify(conn)['records'] == 1
    assert list(conn.execute('SELECT * FROM source_archive')) == archives
    assert list(conn.execute('SELECT * FROM alert_outbox')) == before_alerts
    assert list(conn.execute('SELECT * FROM model_validation')) == before_gates
    receipt = edge_enrichment.checkpoint(owner[0])
    assert receipt['db_sha256'] == pilot.file_hash(owner[0])
    assert 'PROSPECTIVE-' not in (tmp_path / 'second.json').read_text(encoding='utf-8')
