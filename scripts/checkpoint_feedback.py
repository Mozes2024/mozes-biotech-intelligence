"""Trusted Actions relay: verify downloaded durable bytes before feedback.

No restore, producer mutation, ACK, notification or workflow dispatch. Errors are
sanitized; the shared Edge control secret exists only in the final POST step.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .restore_pages_state import REPO, api, collection, gh, require, validate

SYNC_URL = 'https://mozes-hot-clock.sp500.workers.dev/edge/sync'
TARGET = 'https://mozes-hot-clock.sp500.workers.dev/edge/checkpoint-feedback'


def evidence(*, now=None):
    """Proof is about this successful monitor job, not a dispatched request."""
    now = now or datetime.now(timezone.utc)
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    sha = os.environ.get('GITHUB_SHA', '')
    artifact_id = os.environ.get('CHECKPOINT_ARTIFACT_ID', '')
    require(os.environ.get('GITHUB_REPOSITORY') == REPO
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_RUN_ATTEMPT') == '1'
            and re.fullmatch(r'[1-9]\d{0,19}', run_id)
            and re.fullmatch(r'[a-f0-9]{40}', sha)
            and re.fullmatch(r'[1-9]\d{0,19}', artifact_id), 'untrusted_context')
    run = api(f'runs/{run_id}')
    require(str(run.get('id')) == run_id and run.get('head_sha') == sha
            and run.get('head_branch') == 'main' and run.get('run_attempt') == 1
            and run.get('head_repository', {}).get('full_name') == REPO
            and run.get('name') == 'lightweight-live-monitor'
            and run.get('path') == '.github/workflows/lightweight-monitor.yml'
            and run.get('event') in ('schedule', 'workflow_dispatch'), 'untrusted_run')
    jobs = collection(api(f'runs/{run_id}/jobs?filter=all&per_page=100'), 'jobs')
    monitors = [j for j in jobs if j.get('name') == 'monitor']
    require(len(monitors) == 1 and monitors[0].get('status') == 'completed'
            and monitors[0].get('conclusion') == 'success', 'monitor_not_successful')
    incidents = [j for j in jobs if j.get('name') == 'incident-eligibility']
    require(len(incidents) == 1 and incidents[0].get('status') == 'completed'
            and incidents[0].get('conclusion') == 'skipped', 'incident_not_excluded')
    for name in ('Bind durable checkpoint to monitor database', 'Run actions/upload-artifact@v4'):
        steps = [s for s in monitors[0].get('steps', []) if s.get('name') == name]
        require(len(steps) == 1 and steps[0].get('status') == 'completed'
                and steps[0].get('conclusion') == 'success', 'checkpoint_step_failed')
    artifacts = collection(api(f'runs/{run_id}/artifacts?per_page=100'), 'artifacts')
    selected = [a for a in artifacts if a.get('name') == 'mozes-live-monitor']
    require(len(selected) == 1 and selected[0].get('id') == int(artifact_id), 'artifact_identity')
    artifact = selected[0]
    owner = artifact.get('workflow_run', {})
    require(artifact.get('expired') is False and artifact.get('size_in_bytes', 0) > 0
            and owner.get('id') == int(run_id) and owner.get('head_sha') == sha
            and owner.get('head_branch') == 'main', 'artifact_provenance')
    with tempfile.TemporaryDirectory() as tmp:
        gh('run', 'download', run_id, '--repo', REPO, '-n', 'mozes-live-monitor', '-D', tmp)
        _, receipt = validate(Path(tmp), run, artifact, now)
    require(not receipt['stale'] and datetime.fromisoformat(receipt['checkpoint_at']) <= now
            and datetime.fromisoformat(artifact['created_at'].replace('Z', '+00:00')) <= now,
            'stale_or_future_checkpoint')
    return {'schema': 1, 'repository': REPO, 'workflow': 'lightweight-monitor.yml',
            'run_id': run_id, 'run_attempt': 1, 'head_sha': sha,
            'artifact_id': artifact['id'], 'artifact_created_at': artifact['created_at'],
            'checkpoint_at': receipt['checkpoint_at'], 'db_sha256': receipt['db_sha256'],
            'proof': 'actions_download_sha256_sqlite_v1'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send(receipt, *, opener=None):
    require(os.environ.get('MOZES_EDGE_SYNC_URL') == SYNC_URL, 'edge_origin_mismatch')
    token = os.environ.get('EDGE_SYNC_TOKEN', '')
    require(bool(token), 'edge_secret_missing')
    request = urllib.request.Request(TARGET, data=json.dumps(receipt).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                 'User-Agent': 'MOZES-CheckpointFeedback/1.0'})
    opener = opener or urllib.request.build_opener(NoRedirect()).open
    try:
        with opener(request, timeout=15) as response:
            require(response.status == 200, 'feedback_http_status')
            raw = response.read(4097)
            require(len(raw) <= 4096, 'feedback_response_size')
            body = json.loads(raw)
            require(body.get('ok') is True and body.get('status') in ('checkpointed', 'duplicate'),
                    'feedback_response_shape')
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'feedback_http_{exc.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('feedback_network_or_timeout') from None


def main():
    try:
        send(evidence())
    except Exception as exc:
        # Never emit traceback/provider bodies/credential-bearing exception text.
        allowed = {'untrusted_context', 'untrusted_run', 'monitor_not_successful',
                   'incident_not_excluded', 'checkpoint_step_failed', 'artifact_identity',
                   'artifact_provenance', 'checkpoint_integrity', 'manifest_provenance',
                   'checkpoint_chronology', 'sqlite_integrity', 'sqlite_foreign_keys',
                   'stale_or_future_checkpoint', 'edge_origin_mismatch', 'edge_secret_missing',
                   'feedback_http_status', 'feedback_response_size', 'feedback_response_shape',
                   'feedback_network_or_timeout', 'incomplete_inventory', 'artifact_layout'}
        category = str(exc) if type(exc) is RuntimeError else ''
        if category not in allowed and not re.fullmatch(r'feedback_http_[1-5]\d\d', category):
            category = 'unexpected_validation_error'
        print('checkpoint_feedback FAIL ' + category)
        return 1
    print('checkpoint_feedback PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
