"""Synthetic GitHub boundary, deliberately independent of every SQLite checkpoint."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from mozes import prospective_authorization as auth

CODE = 'a' * 40


class GitHub:
    def __init__(self):
        self.runs, self.jobs, self.refs = {}, {}, {}
        self.commits = {CODE: {'sha': CODE, 'tree': {'sha': 'b' * 40}, 'parents': []}}
        self.calls = []
        self.fail = None
        self.now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)

    def add_run(self, run_id, authorization, *, attempt=1, status='in_progress'):
        issued = datetime.strptime(authorization.split('.')[0], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        self.runs[str(run_id)] = {'id': int(run_id), 'run_attempt': attempt, 'head_sha': CODE,
            'head_branch': 'main', 'head_repository': {'full_name': auth.REPO}, 'path': auth.PATH,
            'name': auth.WORKFLOW, 'event': 'schedule', 'status': status,
            'created_at': (issued + timedelta(microseconds=1)).isoformat()}
        self.jobs[str(run_id)] = [
            {'id': int(run_id) * 10 + 1, 'run_id': int(run_id), 'run_attempt': attempt,
             'name': auth.JOB, 'status': 'in_progress', 'conclusion': None,
             'steps': [{'name': auth.STEP + authorization, 'status': 'in_progress',
                        'conclusion': None, 'started_at': self.now.isoformat()}]},
            {'id': int(run_id) * 10 + 2, 'run_id': int(run_id), 'run_attempt': attempt,
             'name': 'monitor', 'status': 'queued', 'conclusion': None,
             'steps': [{'name': auth.PERMIT_STEP, 'status': 'queued', 'conclusion': None}]}]
        if status in ('queued', 'pending'):
            self.jobs[str(run_id)][0]['status'] = 'queued'
            self.jobs[str(run_id)][0]['steps'][0].update(status='queued', started_at=None)

    def completed_claim(self, run_id, *, success=True):
        job, monitor = self.jobs[str(run_id)]
        job.update(status='completed', conclusion='success' if success else 'failure')
        job['steps'][0].update(status='completed', conclusion=job['conclusion'])
        monitor['status'] = 'in_progress'
        monitor['steps'][0]['status'] = 'in_progress'

    def api(self, path, *, body=None, absent_ok=False):
        self.calls.append((path, body))
        if self.fail == ('before', path):
            raise auth.AuthorizationError('synthetic ambiguous API failure')
        if path == 'commits/main':
            return {'sha': CODE}
        if path.startswith('actions/workflows/'):
            q = parse_qs(urlsplit(path).query)
            low, high = map(auth.stamp, q['created'][0].split('..'))
            rows = [r for r in self.runs.values() if low <= auth.stamp(r['created_at']) < high
                    and r['head_sha'] == q['head_sha'][0] and r['event'] == q['event'][0]]
            return {'total_count': len(rows), 'workflow_runs': rows}
        if path.startswith('actions/runs/'):
            run_id = path.split('/')[2]
            if '/jobs?' in path:
                rows = self.jobs[run_id]
                return {'total_count': len(rows), 'jobs': rows}
            return self.runs[run_id]
        if path.startswith('git/ref/'):
            value = self.refs.get(path[len('git/ref/'):])
            if value is None and not absent_ok:
                raise auth.AuthorizationError('synthetic missing ref')
            return value
        if path.startswith('git/commits/'):
            return self.commits[path.split('/')[-1]]
        if path == 'git/commits':
            sha = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()
            result = {'sha': sha, 'message': body['message'], 'tree': {'sha': body['tree']},
                      'parents': [{'sha': p} for p in body['parents']]}
            self.commits[sha] = result
            return result
        if path == 'git/refs':
            key = body['ref'][len('refs/'):]
            if key in self.refs:
                raise auth.AuthorizationError('synthetic create-only ref conflict')
            result = {'ref': body['ref'], 'object': {'sha': body['sha'], 'type': 'commit'}}
            self.refs[key] = result
            if self.fail == ('after', path):
                raise auth.AuthorizationError('synthetic ambiguous API failure')
            return result
        raise AssertionError('unexpected synthetic GitHub call: ' + path)


def issue_permit(monkeypatch, root, *, run_id='100', now='2026-10-15T12:00:00Z', github=None):
    server = github or getattr(monkeypatch, '_prospective_github', None) or GitHub()
    monkeypatch._prospective_github = server
    server.now = auth.stamp(now)
    issued = (server.now - timedelta(seconds=1)).strftime('%Y%m%dT%H%M%SZ')
    identifier = issued + '.' + CODE + '.' + f'{len(server.runs) + 1:032x}'
    for name, value in {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': auth.REPO,
        'GITHUB_WORKFLOW': auth.WORKFLOW, 'GITHUB_REF': 'refs/heads/main',
        'GITHUB_EVENT_NAME': 'schedule', 'GITHUB_RUN_ID': run_id, 'GITHUB_RUN_ATTEMPT': '1',
        'GITHUB_SHA': CODE, 'GITHUB_JOB': 'prospective_claim',
        'MOZES_PROSPECTIVE_CAPTURE': '1', 'MOZES_PROSPECTIVE_CLEANUP_READY': 'true',
        'MOZES_PROSPECTIVE_EVENT_IDS': json.dumps([auth.EVENT]),
        'MOZES_PROSPECTIVE_AUTHORIZATION': identifier}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('SOURCE_RUN_ID', raising=False)
    monkeypatch.delenv('INCIDENT_MODE', raising=False)
    monkeypatch.delenv('MOZES_PROSPECTIVE_PREPARE', raising=False)
    claim_temp = root / ('claim-' + run_id + '-' + str(len(server.runs)))
    claim_temp.mkdir()
    monkeypatch.setenv('RUNNER_TEMP', str(claim_temp))
    monkeypatch.setattr(auth, 'api', server.api)
    monkeypatch.setattr(auth, 'utcnow', lambda: server.now)
    server.add_run(run_id, identifier)
    claim_sha = auth.claim()
    server.completed_claim(run_id)
    monitor_temp = root / ('monitor-' + run_id + '-' + str(len(server.runs)))
    monitor_temp.mkdir()
    monkeypatch.setenv('RUNNER_TEMP', str(monitor_temp))
    monkeypatch.setenv('GITHUB_JOB', 'monitor')
    monkeypatch.setenv('MOZES_PROSPECTIVE_CLAIM_SHA', claim_sha)
    auth.permit()
    return server
