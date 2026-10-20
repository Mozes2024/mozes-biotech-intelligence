"""Bounded Pages handoff. Requests never acknowledge source freshness."""
from __future__ import annotations
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = 'Mozes2024/mozes-biotech-intelligence'
ACTIVE = ('queued', 'in_progress', 'waiting', 'pending', 'requested')


def require(condition, category):
    if not condition:
        raise RuntimeError(category)


def stamp(value):
    at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    require(at.tzinfo is not None, 'publication_chronology')
    return at


def verify_receipt(repo, run, value, gh):
    """Only an immutable artifact from a verified deployment acknowledges cadence."""
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success'
            and run.get('run_attempt') == 1 and run.get('head_branch') == 'main'
            and run.get('head_repository', {}).get('full_name') == repo
            and run.get('path') == '.github/workflows/pages.yml'
            and run.get('event') in ('workflow_dispatch', 'workflow_run'), 'publication_owner')
    require(value.get('schema') == 2 and str(run['id']) == value.get('pages_run_id')
            and run.get('head_sha') == value.get('code_sha')
            and re.fullmatch('[a-f0-9]{64}', value.get('change_ledger', ''))
            and re.fullmatch('[a-f0-9]{64}', value.get('fingerprint', '')), 'publication_identity')
    checkpoint = value.get('checkpoint', {})
    require(checkpoint.get('schema') == 1 and checkpoint.get('consumer_only') is True
            and str(checkpoint.get('source_run_id', '')).isdigit()
            and type(checkpoint.get('artifact_id')) is int
            and re.fullmatch('[a-f0-9]{64}', checkpoint.get('db_sha256', '')), 'publication_checkpoint')
    jobs = json.loads(gh('api', f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=all&per_page=100"))
    require(jobs.get('total_count') == len(jobs.get('jobs', [])), 'publication_inventory')
    deploy = [j for j in jobs['jobs'] if j.get('name') == 'deploy']
    require(len(deploy) == 1 and deploy[0].get('conclusion') == 'success', 'publication_deploy')
    steps = deploy[0].get('steps', [])
    for name in ('Deploy GitHub Pages', 'Acknowledge ledger only after successful deployment'):
        matches = [s for s in steps if s.get('name') == name]
        require(len(matches) == 1 and matches[0].get('status') == 'completed'
                and matches[0].get('conclusion') == 'success', 'publication_deploy')
    inventory = json.loads(gh('api', f"repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100"))
    require(inventory.get('total_count') == len(inventory.get('artifacts', [])), 'publication_inventory')
    ledgers = [a for a in inventory['artifacts'] if a.get('name') == 'mozes-published-ledger']
    require(len(ledgers) == 1 and ledgers[0].get('expired') is False
            and ledgers[0].get('size_in_bytes', 0) > 0
            and ledgers[0].get('workflow_run', {}).get('id') == run['id'], 'publication_artifact')
    require(stamp(checkpoint['checkpoint_at']) <= stamp(deploy[0]['started_at'])
            <= stamp(ledgers[0]['created_at']) <= stamp(run['updated_at']), 'publication_chronology')


def dispatch_decision(rows, *, now):
    require(all(r.get('status') in ('completed', *ACTIVE) for r in rows), 'publication_inventory')
    if any(r['status'] in ACTIVE for r in rows):
        return 'publish_running'
    # PR CI creates completed/skipped Pages runs without a publication request.
    # They must not restart the two-minute dispatch throttle or act as an ACK.
    rows = [r for r in rows if r.get('conclusion') != 'skipped']
    if rows and (now - max(stamp(r['created_at']) for r in rows)).total_seconds() < 120:
        return 'publish_requested'
    return 'publish_requested_new'


def dispatch():
    # All ordinary callers run under the existing serialized hot-monitor lock.
    # Complete inventory also catches old queued requests; no deployment is cancelled.
    from scripts.restore_pages_state import inventory, gh
    require(os.environ.get('GITHUB_REPOSITORY') == REPO, 'repository_mismatch')
    rows = inventory('workflows/pages.yml/runs?exclude_pull_requests=true', 'workflow_runs')
    require(all(r.get('head_repository', {}).get('full_name') == REPO
                and r.get('head_branch') == 'main' and r.get('path') == '.github/workflows/pages.yml'
                for r in rows), 'publication_owner')
    state = dispatch_decision(rows, now=datetime.now(timezone.utc))
    if state == 'publish_requested_new':
        gh('workflow', 'run', 'pages.yml', '--repo', REPO, '--ref', 'main',
           '-f', f"source_run_id={os.environ['GITHUB_RUN_ID']}", timeout=30)
        state = 'publish_requested'
    elif state == 'publish_running':
        print('publication_blocked category=pages_running; monitoring continues')
    terminal = sorted((r for r in rows if r['status'] == 'completed' and r.get('conclusion') != 'skipped'), key=lambda r: stamp(r['created_at']), reverse=True)
    if terminal:
        print('last_attempt=' + ('publish_requested' if terminal[0].get('conclusion') == 'success' else 'publish_failed'))
    receipt_path = Path('.monitor/published-ledger.json')
    if receipt_path.exists():
        value = json.loads(receipt_path.read_text())
        if value.get('schema') == 2:
            print('publication_ack=publish_succeeded checkpoint=' + value['checkpoint']['checkpoint_at'])
    print('publication_state=' + state)


def preflight(receipt_path='/tmp/mozes-published/publish-receipt.json'):
    """Recheck source movement and published ordering immediately before deployment."""
    import time
    from scripts import restore_pages_state as pages
    from scripts.restore_pages_state import newest, gh
    pages.RESTORE_DEADLINE = time.monotonic() + 180
    try:
        from scripts.restore_monitor_state import restore_published_receipt
        value = json.loads(Path(receipt_path).read_text())
        current = json.loads(gh('api', f'repos/{REPO}/git/ref/heads/main', timeout=30))['object']['sha']
        require(current == value['code_sha'], 'publication_code_superseded')
        run, artifact = newest()
        checkpoint = value['checkpoint']
        require(str(run['id']) == checkpoint['source_run_id'] and artifact['id'] == checkpoint['artifact_id'],
                'checkpoint_changed')
        require(restore_published_receipt(REPO, runner=gh) is not None, 'publication_success_unknown')
        path = Path('.monitor/published-ledger.json')
        if path.exists():
            previous = json.loads(path.read_text())
            if previous.get('schema') == 2:
                require(stamp(checkpoint['checkpoint_at']) >= stamp(previous['checkpoint']['checkpoint_at']),
                        'publication_downgrade')
        print('publication_preflight PASS')
    finally:
        pages.RESTORE_DEADLINE = None


if __name__ == '__main__':
    import sys
    {'dispatch': dispatch, 'preflight': preflight}[sys.argv[1]]()
