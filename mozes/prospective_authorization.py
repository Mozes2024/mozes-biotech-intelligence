"""One scheduled pilot attempt; permanent Git latch, never a SQLite lease.

Only the isolated claim job writes Git. Monitor permit verification is read-only;
the observer consumes a runner-local permit before its first evidence mutation.
All API failures are terminal. No existing ref is adopted, updated or deleted.
"""
import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

REPO = 'Mozes2024/mozes-biotech-intelligence'
WORKFLOW = 'lightweight-live-monitor'
PATH = '.github/workflows/lightweight-monitor.yml'
EVENT = 'PHAR-SNDA-LOWER-WEIGHT'
SCOPE = 'single-event-scheduled-attempt-v1'
JOB = 'prospective-pilot-claim'
STEP = 'Acquire one-shot prospective claim '
PERMIT_STEP = 'Verify one-shot prospective permit'
WINDOW = timedelta(hours=12)
utcnow = lambda: datetime.now(timezone.utc)


class AuthorizationError(ValueError):
    """Static private reason, never an API response or token."""


def require(value, reason):
    if not value:
        raise AuthorizationError(reason)


def api(path, *, body=None, absent_ok=False):
    args = ['gh', 'api', f'repos/{REPO}/{path}']
    if body is not None:
        args += ['--include', '--method', 'POST', '--input', '-']
    try:
        result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            if absent_ok and '(HTTP 404)' in result.stderr:
                return None
            raise AuthorizationError('claim_API_failed_or_ambiguous_no_retry')
        output = result.stdout
        if body is not None:
            headers, output = output.split('\n\n', 1)
            require(headers.splitlines()[0].split()[1] == '201', 'claim_creation_not_confirmed')
        return json.loads(output)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise AuthorizationError('claim_API_failed_or_ambiguous_no_retry') from None


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(result.tzinfo is not None, 'claim_timestamp_required')
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        raise AuthorizationError('claim_timestamp_required') from None


def identity(job):
    auth = os.environ.get('MOZES_PROSPECTIVE_AUTHORIZATION', '')
    match = re.fullmatch(r'(\d{8}T\d{6}Z)\.([a-f0-9]{40})\.([a-f0-9]{32})', auth)
    require(match, 'one_shot_authorization_required')
    try:
        issued = datetime.strptime(match[1], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise AuthorizationError('one_shot_authorization_required') from None
    require(issued <= utcnow() < issued + WINDOW, 'one_shot_authorization_expired_or_future')
    require(os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == REPO
            and os.environ.get('GITHUB_WORKFLOW') == WORKFLOW
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') == 'schedule'
            and os.environ.get('GITHUB_JOB') == job
            and os.environ.get('GITHUB_RUN_ATTEMPT') == '1'
            and re.fullmatch('[1-9][0-9]*', os.environ.get('GITHUB_RUN_ID', ''))
            and os.environ.get('GITHUB_SHA') == match[2]
            and os.environ.get('MOZES_PROSPECTIVE_CAPTURE') == '1'
            and not os.environ.get('MOZES_PROSPECTIVE_PREPARE')
            and os.environ.get('INCIDENT_MODE') != 'recover'
            and not os.environ.get('SOURCE_RUN_ID'), 'one_shot_owner_or_attempt_mismatch')
    try:
        require(json.loads(os.environ.get('MOZES_PROSPECTIVE_EVENT_IDS', '[]')) == [EVENT],
                'one_shot_event_scope_mismatch')
    except ValueError:
        raise AuthorizationError('one_shot_event_scope_mismatch') from None
    return {'schema': 1, 'authorization_id': auth, 'event_id': EVENT, 'scope': SCOPE,
            'run_id': os.environ['GITHUB_RUN_ID'], 'run_attempt': 1,
            'code_sha': match[2], 'repository': REPO, 'workflow': PATH}, issued


def local_path(record, suffix):
    root = Path(os.environ.get('RUNNER_TEMP', ''))
    require(root.is_absolute() and root.is_dir(), 'one_shot_runner_temp_required')
    # Deliberately outside restored DB/checkpoint/artifact paths.
    return root / ('mozes-prospective-' + record['authorization_id'] + '-' + suffix)


def exclusive(record, suffix, value):
    try:
        with local_path(record, suffix).open('x', encoding='utf-8') as fh:
            fh.write(json.dumps(value, sort_keys=True))
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        raise AuthorizationError('one_shot_local_attempt_already_used_or_unavailable') from None


def collection(value, key):
    rows = value.get(key)
    require(isinstance(rows, list) and type(value.get('total_count')) is int
            and value['total_count'] == len(rows) <= 100
            and all(type(r.get('id')) is int and r['id'] > 0 for r in rows)
            and len({r['id'] for r in rows}) == len(rows), 'one_shot_inventory_incomplete')
    return rows


def context(record, issued, *, claiming):
    require(api('commits/main').get('sha') == record['code_sha'], 'one_shot_main_changed')
    run = api('actions/runs/' + record['run_id'])
    require(type(run.get('run_attempt')) is int and run['run_attempt'] == 1
            and str(run.get('id')) == record['run_id']
            and run.get('head_sha') == record['code_sha'] and run.get('head_branch') == 'main'
            and (run.get('head_repository') or {}).get('full_name') == REPO
            and run.get('path') == PATH and run.get('name') == WORKFLOW
            and run.get('event') == 'schedule' and run.get('status') == 'in_progress'
            and issued <= stamp(run.get('created_at')) < issued + WINDOW,
            'one_shot_run_identity_mismatch')
    jobs = collection(api(f'actions/runs/{record["run_id"]}/jobs?filter=all&per_page=100'), 'jobs')
    claims = [j for j in jobs if j.get('name') == JOB]
    require(len(claims) == 1, 'one_shot_claim_job_ambiguous')
    claim = claims[0]
    require(type(claim.get('run_attempt')) is int and claim['run_attempt'] == 1
            and str(claim.get('run_id')) == record['run_id'],
            'one_shot_claim_job_ownership')
    steps = [s for s in claim.get('steps', []) if s.get('name') == STEP + record['authorization_id']]
    require(len(steps) == 1, 'one_shot_claim_step_missing')
    if claiming:
        require(claim.get('status') == steps[0].get('status') == 'in_progress',
                'one_shot_claim_step_not_running')
    else:
        require(claim.get('conclusion') == steps[0].get('conclusion') == 'success'
                and claim.get('status') == steps[0].get('status') == 'completed',
                'one_shot_claim_not_successful')
        monitors = [j for j in jobs if j.get('name') == 'monitor']
        require(len(monitors) == 1 and type(monitors[0].get('run_attempt')) is int
                and monitors[0]['run_attempt'] == 1
                and str(monitors[0].get('run_id')) == record['run_id']
                and monitors[0].get('status') == 'in_progress', 'one_shot_monitor_ownership')
        permits = [s for s in monitors[0].get('steps', []) if s.get('name') == PERMIT_STEP]
        require(len(permits) == 1 and permits[0].get('status') == 'in_progress',
                'one_shot_permit_step_not_running')
    return {**record, 'claim_job_id': claim['id']}


def no_prior_attempt(record, issued):
    # The immutable ID includes both code SHA and issue time. Neither the audit
    # window nor approved code can be moved forward while reusing the same ID.
    query = urlencode({'event': 'schedule', 'head_sha': record['code_sha'],
        'created': issued.isoformat() + '..' + (issued + WINDOW).isoformat(), 'per_page': 100})
    runs = collection(api('actions/workflows/lightweight-monitor.yml/runs?' + query), 'workflow_runs')
    require(sum(str(r['id']) == record['run_id'] for r in runs) == 1, 'one_shot_current_run_missing')
    current = next(r for r in runs if str(r['id']) == record['run_id'])
    for run in runs:
        require(run.get('head_sha') == record['code_sha'] and run.get('head_branch') == 'main'
                and (run.get('head_repository') or {}).get('full_name') == REPO
                and run.get('event') == 'schedule' and run.get('path') == PATH
                and issued <= stamp(run.get('created_at')) < issued + WINDOW,
                'one_shot_untrusted_history')
        if str(run['id']) == record['run_id']:
            continue
        jobs = collection(api(f'actions/runs/{run["id"]}/jobs?filter=all&per_page=100'), 'jobs')
        claims = [j for j in jobs if j.get('name') == JOB]
        require(len(claims) <= 1 and (claims or run.get('status') in ('queued', 'pending')),
                'one_shot_prior_job_unknown')
        for job in claims:
            if job.get('conclusion') == 'skipped' and not job.get('steps'):
                continue
            if job.get('status') == 'queued':
                require((stamp(run['created_at']), run['id']) >
                        (stamp(current['created_at']), current['id']), 'one_shot_earlier_claim_uncertain')
            steps = job.get('steps', [])
            same = [s for s in steps if s.get('name') == STEP + record['authorization_id']]
            if not same and len([s for s in steps if s.get('name', '').startswith(STEP)]) == 1:
                continue  # A different authorization cannot have passed our exact-step check.
            require(not any(s.get('started_at') or s.get('status') == 'in_progress'
                            or s.get('conclusion') not in (None, 'skipped') for s in same),
                    'one_shot_prior_claim_attempted')
            require(job.get('status') == 'queued' or job.get('conclusion') == 'success'
                    and same and all(s.get('conclusion') == 'skipped' for s in same),
                    'one_shot_prior_claim_uncertain')


def ref_name(record):
    return 'tags/prospective-pilot-' + record['authorization_id']


def claim():
    record, issued = identity('prospective_claim')
    exclusive(record, 'claim-attempt', record)
    record = context(record, issued, claiming=True)
    no_prior_attempt(record, issued)
    require(api('git/ref/' + ref_name(record), absent_ok=True) is None, 'one_shot_claim_exists')
    head = api('git/commits/' + record['code_sha'])
    commit = api('git/commits', body={'message': json.dumps(record, sort_keys=True),
                 'tree': head['tree']['sha'], 'parents': [record['code_sha']]})
    require(re.fullmatch('[a-f0-9]{40}', commit.get('sha', '')), 'one_shot_commit_ambiguous')
    created = api('git/refs', body={'ref': 'refs/' + ref_name(record), 'sha': commit['sha']})
    require(created.get('ref') == 'refs/' + ref_name(record)
            and created.get('object', {}).get('sha') == commit['sha']
            and created.get('object', {}).get('type') == 'commit', 'one_shot_claim_ambiguous')
    return commit['sha']


def permit():
    record, issued = identity('monitor')
    exclusive(record, 'permit-attempt', record)
    record = context(record, issued, claiming=False)
    claim_sha = os.environ.get('MOZES_PROSPECTIVE_CLAIM_SHA', '')
    require(re.fullmatch('[a-f0-9]{40}', claim_sha), 'one_shot_confirmed_claim_required')
    ref = api('git/ref/' + ref_name(record))
    require(ref.get('ref') == 'refs/' + ref_name(record)
            and ref.get('object', {}).get('sha') == claim_sha
            and ref.get('object', {}).get('type') == 'commit', 'one_shot_claim_changed')
    commit = api('git/commits/' + claim_sha)
    require(json.loads(commit['message']) == record
            and [p['sha'] for p in commit['parents']] == [record['code_sha']], 'one_shot_claim_ownership')
    head = api('git/commits/' + record['code_sha'])
    require(commit['tree']['sha'] == head['tree']['sha'], 'one_shot_claim_tree_changed')
    exclusive(record, 'permit', {'claim_sha': claim_sha, 'record': record})


def consume():
    record, _ = identity('monitor')
    # Burn BEFORE reading the permit/entering the observer. A crash cannot make
    # this step retryable, and restoring a DB cannot recreate this local file.
    exclusive(record, 'consumed', record)
    try:
        path = local_path(record, 'permit')
        require(path.stat().st_size <= 8192, 'one_shot_permit_invalid')
        value = json.loads(path.read_text(encoding='utf-8'))
        require(set(value) == {'claim_sha', 'record'}
                and re.fullmatch('[a-f0-9]{40}', value['claim_sha'])
                and value['claim_sha'] == os.environ.get('MOZES_PROSPECTIVE_CLAIM_SHA')
                and set(value['record']) == set(record) | {'claim_job_id'}
                and type(value['record']['claim_job_id']) is int and value['record']['claim_job_id'] > 0
                and all(value['record'][k] == v for k, v in record.items()), 'one_shot_permit_ownership')
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise AuthorizationError('one_shot_permit_missing_or_invalid') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('claim', 'permit'))
    args = parser.parse_args()
    try:
        if args.action == 'claim':
            sha = claim()
            with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as out:
                out.write('claim_sha=' + sha + '\n')
        else:
            permit()
    except (AuthorizationError, OSError, ValueError, KeyError, TypeError, AttributeError):
        print('prospective one-shot denied; authorization is not retryable')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
