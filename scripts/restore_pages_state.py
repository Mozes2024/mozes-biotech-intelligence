"""Read-only consumer of the newest durable lightweight checkpoint, never a producer."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = 'Mozes2024/mozes-biotech-intelligence'
WORKFLOW = 'lightweight-monitor.yml'
ACTIVE = ('queued', 'in_progress', 'waiting', 'pending', 'requested')
RESTORE_ATTEMPTS = 3
RESELECT_WAIT_SECONDS = 20


def gh(*args, timeout=300):
    return subprocess.check_output(['gh', *args], text=True, timeout=timeout)


def _parse_iso(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def require(condition, category):
    if not condition:
        raise RuntimeError(category)


def api(path):
    return json.loads(gh('api', f'repos/{REPO}/actions/{path}', timeout=30))


def collection(value, key, *, partial=False):
    rows = value.get(key)
    require(isinstance(rows, list) and type(value.get('total_count')) is int
            and (value['total_count'] >= len(rows) if partial else value['total_count'] == len(rows)), 'incomplete_inventory')
    return rows


def inventory(path, key, bound=None):
    rows = []
    expected = None
    deadline = time.monotonic() + 90
    page = 1
    while bound is None or page <= bound:
        require(time.monotonic() < deadline, 'inventory_time_budget')
        result = api(f'{path}&per_page=100&page={page}')
        require(time.monotonic() < deadline, 'inventory_time_budget')
        batch = collection(result, key, partial=True)
        if expected is None:
            expected = result['total_count']
        require(result['total_count'] == expected, 'inventory_changed')
        require(len(batch) <= 100 and all(type(r.get('id')) is int and r['id'] > 0 for r in batch),
                'incomplete_inventory')
        rows.extend(batch)
        if len(rows) == expected:
            require(len({r['id'] for r in rows}) == len(rows), 'duplicate_inventory')
            return rows
        # A fixed advertised count bounds this complete scan. Never stop at a
        # recent creation date: an old queued run/rerun can become a new writer.
        require(batch and len(rows) < expected and page < (expected + 99) // 100,
                'incomplete_inventory')
        page += 1
    raise RuntimeError('producer_inventory_bound')


def newest():
    """Order by durable artifact creation, never by dispatch/run creation order.

    Include expired artifacts and older dispatched waiters. Successful checkpoint
    steps without their artifact block publication rather than rewind it.
    """
    artifacts = inventory('artifacts?name=mozes-live-monitor', 'artifacts', 15)
    require(artifacts and all(a.get('name') == 'mozes-live-monitor' and _parse_iso(a.get('created_at'))
                              for a in artifacts), 'artifact_inventory')
    artifacts.sort(key=lambda a: _parse_iso(a['created_at']), reverse=True)
    artifact = artifacts[0]
    require(len(artifacts) == 1 or artifacts[1]['created_at'] != artifact['created_at'], 'ambiguous_checkpoint')
    require(artifact.get('expired') is False and artifact.get('size_in_bytes', 0) > 0,
            'newest_artifact_expired_or_empty')
    owner = artifact.get('workflow_run', {}).get('id')
    require(isinstance(owner, int), 'artifact_provenance')
    # Branch-filtered searches are capped by GitHub at 1,000 results. Inventory
    # the unfiltered workflow instead and validate every relevant run's branch.
    rows = inventory(f'workflows/{WORKFLOW}/runs?exclude_pull_requests=true', 'workflow_runs')
    require(all(_parse_iso(r.get('updated_at')) and r.get('status') in ('completed', *ACTIVE)
                for r in rows), 'incomplete_inventory')
    relevant = [r for r in rows if r['id'] == owner or r['status'] in ACTIVE
                or _parse_iso(r['updated_at']) >= _parse_iso(artifact['created_at'])]
    require(len(relevant) <= 40 and sum(r['id'] == owner for r in relevant) == 1, 'producer_inventory_bound')
    selected = None
    for run in relevant:
        require(run.get('head_branch') == 'main' and run.get('head_repository', {}).get('full_name') == REPO
                and run.get('name') == 'lightweight-live-monitor'
                and run.get('path') == '.github/workflows/lightweight-monitor.yml'
                and run.get('event') in ('schedule', 'workflow_dispatch')
                and re.fullmatch('[a-f0-9]{40}', run.get('head_sha') or ''), 'untrusted_producer')
        require(run.get('run_attempt') == 1, 'rerun_requires_review')
        require(run.get('status') in ('completed', 'queued', 'in_progress', 'waiting', 'pending', 'requested'),
                'unknown_producer_status')
        if run['status'] == 'completed':
            require(run.get('conclusion') in ('success', 'failure', 'cancelled'), 'unknown_producer_conclusion')
        jobs = collection(api(f"runs/{run['id']}/jobs?filter=all&per_page=100"), 'jobs')
        # A live owner is a transient publishing conflict, not proof that the
        # last checkpoint is invalid. Wait, then redo the complete selection.
        require(run['status'] == 'completed', 'producer_busy')
        monitors = [j for j in jobs if j.get('name') == 'monitor']
        require(len(monitors) <= 1, 'ambiguous_monitor')
        if not monitors:
            require(run['status'] == 'completed' and jobs and all(j.get('status') == 'completed' for j in jobs),
                    'incomplete_producer')
            # No processing job means no checkpoint. Unknown job names are not trusted.
            require(all(j.get('name') == 'incident-eligibility' for j in jobs), 'unknown_producer_job')
            continue
        steps = monitors[0].get('steps')
        require(isinstance(steps, list), 'incomplete_producer')
        bound = [s for s in steps if s.get('name') == 'Bind durable checkpoint to monitor database']
        upload = [s for s in steps if s.get('name') == 'Run actions/upload-artifact@v4']
        require(len(bound) == 1 and len(upload) == 1, 'incomplete_producer')
        candidates = [a for a in artifacts if a.get('workflow_run', {}).get('id') == run['id']]
        if candidates or bound[0].get('conclusion') == 'success':
            require(run['status'] == 'completed' and all(j.get('status') == 'completed' for j in jobs)
                    and bound[0].get('status') == upload[0].get('status') == 'completed'
                    and bound[0].get('conclusion') == upload[0].get('conclusion') == 'success', 'incomplete_checkpoint')
            require(len(candidates) == 1, 'newest_artifact_missing_or_ambiguous')
            if run['id'] == owner:
                require(artifact.get('workflow_run', {}).get('head_branch') == 'main'
                        and artifact.get('workflow_run', {}).get('head_sha') == run['head_sha'], 'artifact_provenance')
                selected = run
        else:
            require(run['status'] == 'completed' and all(j.get('status') == 'completed' for j in jobs)
                    and bound[0].get('conclusion') in ('skipped', 'failure', 'cancelled'), 'incomplete_producer')
    require(selected is not None, 'producer_missing')
    return selected, artifact


def unique(root, name):
    matches = list(root.rglob(name))
    require(len(matches) == 1 and matches[0].is_file() and not matches[0].is_symlink(), 'artifact_layout')
    return matches[0]


def validate(root, run, artifact, now):
    source = unique(root, 'mozes-live.db')
    checkpoint = json.loads(unique(root, 'edge-checkpoint.json').read_text(encoding='utf-8'))
    manifest = json.loads(unique(root, 'manifest.json').read_text(encoding='utf-8'))
    require(checkpoint.get('schema') == 1 and str(checkpoint.get('producer_run_id')) == str(run['id'])
            and checkpoint.get('db_sha256') == hashlib.sha256(source.read_bytes()).hexdigest(), 'checkpoint_integrity')
    require(manifest.get('schema') == 1 and manifest.get('source_sha') == run['head_sha']
            and manifest.get('data_publishable') is True, 'manifest_provenance')
    times = [_parse_iso(v) for v in (run.get('created_at'), manifest.get('generated_at'),
                                    checkpoint.get('created_at'), artifact.get('created_at'))]
    require(all(times) and times == sorted(times) and times[-1] <= now + timedelta(minutes=5), 'checkpoint_chronology')
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        require(conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'sqlite_integrity')
        require(not conn.execute('PRAGMA foreign_key_check').fetchall(), 'sqlite_foreign_keys')
    age = max(0, int((now - times[2]).total_seconds()))
    return source, {'schema': 1, 'consumer_only': True, 'source_run_id': str(run['id']),
                    'artifact_id': artifact['id'], 'db_sha256': checkpoint['db_sha256'],
                    'checkpoint_at': times[2].isoformat(), 'data_age_seconds': age, 'stale': age > 2700}


def _restore_once(*, now=None):
    require(os.environ.get('GITHUB_REPOSITORY') == REPO, 'repository_mismatch')
    now = now or datetime.now(timezone.utc)
    run, artifact = newest()
    requested = os.environ.get('SOURCE_RUN_ID')
    require(not requested or requested.isdigit(), 'invalid_advisory_run')
    with tempfile.TemporaryDirectory() as tmp:
        gh('run', 'download', str(run['id']), '--repo', REPO, '-n', 'mozes-live-monitor', '-D', tmp)
        source, receipt = validate(Path(tmp), run, artifact, now)
        latest_run, latest_artifact = newest()
        require((latest_run['id'], latest_artifact['id']) == (run['id'], artifact['id']), 'checkpoint_changed')
        destination = Path(os.environ['MOZES_DB_PATH'])
        require(not destination.exists(), 'consumer_destination_exists')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        metadata = Path('.monitor/pages-consumer.json')
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps(receipt), encoding='utf-8')
    print('pages_consumer_restore PASS')
    return receipt


def restore(*, now=None):
    for attempt in range(RESTORE_ATTEMPTS):
        try:
            return _restore_once(now=now)
        except RuntimeError as exc:
            if str(exc) not in ('producer_busy', 'checkpoint_changed', 'inventory_changed'):
                raise
            if attempt == RESTORE_ATTEMPTS - 1:
                raise
            print(f'pages_consumer_restore WAIT {exc} attempt={attempt + 1}')
            time.sleep(RESELECT_WAIT_SECONDS)


if __name__ == '__main__':
    restore()
