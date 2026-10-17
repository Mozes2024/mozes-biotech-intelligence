"""GitHub-hosted OFF-only cleanup; no claim, DB, dispatch or activation path."""
import json
import os
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from mozes import prospective_authorization as auth

CAPTURE = 'MOZES_PROSPECTIVE_CAPTURE'
AUTHORIZATION = 'MOZES_PROSPECTIVE_AUTHORIZATION'
EVENT_IDS = 'MOZES_PROSPECTIVE_EVENT_IDS'
JOB = 'prospective-pilot-cleanup'
utcnow = lambda: datetime.now(timezone.utc)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


urlopen = build_opener(NoRedirect()).open


def variable(name, *, method='GET', value=None):
    """The dedicated Variables-write token can only reach these three endpoints."""
    auth.require(name in (CAPTURE, AUTHORIZATION, EVENT_IDS)
        and (method == 'GET' and value is None
             or method == 'PATCH' and name == CAPTURE and value == '0'
             or method == 'DELETE' and name in (AUTHORIZATION, EVENT_IDS) and value is None),
        'cleanup_write_outside_scope')
    token = os.environ.get('MOZES_PROSPECTIVE_CLEANUP_TOKEN', '')
    auth.require(bool(token), 'cleanup_credential_missing')
    request = Request(f'https://api.github.com/repos/{auth.REPO}/actions/variables/{name}',
        method=method, data=json.dumps({'value': value}).encode() if method == 'PATCH' else None,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=10) as response:
            auth.require(response.status == (200 if method == 'GET' else 204), 'cleanup_API_ambiguous')
            if method == 'GET':
                row = json.loads(response.read(16385))
                auth.require(row.get('name') == name and isinstance(row.get('value'), str)
                    and isinstance(row.get('updated_at'), str), 'cleanup_variable_invalid')
                return row
    except HTTPError as error:
        if method == 'GET' and error.code == 404:
            return None
        raise auth.AuthorizationError('cleanup_API_failed_or_ambiguous_OFF_not_confirmed') from None
    except (OSError, URLError, ValueError):
        raise auth.AuthorizationError('cleanup_API_failed_or_ambiguous_OFF_not_confirmed') from None


def snapshot():
    return {name: variable(name) for name in (CAPTURE, AUTHORIZATION, EVENT_IDS)}


def value(rows, name):
    return rows[name]['value'] if rows[name] is not None else None


def owner():
    auth.require(os.environ.get('GITHUB_ACTIONS') == 'true'
        and os.environ.get('GITHUB_REPOSITORY') == auth.REPO
        and os.environ.get('GITHUB_WORKFLOW') == auth.WORKFLOW
        and os.environ.get('GITHUB_JOB') == 'prospective_cleanup'
        and os.environ.get('GITHUB_REF') == 'refs/heads/main'
        and os.environ.get('GITHUB_EVENT_NAME') in ('schedule', 'workflow_dispatch')
        and re.fullmatch('[1-9][0-9]*', os.environ.get('GITHUB_RUN_ID', '')),
        'cleanup_owner_invalid')
    run = auth.api('actions/runs/' + os.environ['GITHUB_RUN_ID'])
    auth.require(str(run.get('id')) == os.environ['GITHUB_RUN_ID']
        and type(run.get('run_attempt')) is int and run['run_attempt'] >= 1
        and str(run['run_attempt']) == os.environ.get('GITHUB_RUN_ATTEMPT')
        and run.get('head_sha') == os.environ.get('GITHUB_SHA')
        and run.get('head_sha') == auth.api('commits/main').get('sha')
        and run.get('head_branch') == 'main'
        and (run.get('head_repository') or {}).get('full_name') == auth.REPO
        and run.get('path') == auth.PATH and run.get('name') == auth.WORKFLOW
        and run.get('event') == os.environ['GITHUB_EVENT_NAME'] and run.get('status') == 'in_progress',
        'cleanup_run_invalid')
    jobs = auth.collection(auth.api(f'actions/runs/{run["id"]}/jobs?filter=all&per_page=100'), 'jobs')
    own = [j for j in jobs if j.get('name') == JOB]
    auth.require(len(own) == 1 and own[0].get('status') == 'in_progress'
        and own[0].get('run_id') == run['id'] and own[0].get('run_attempt') == run['run_attempt'],
        'cleanup_job_invalid')
    return run


def reason(identifier, issued, code):
    if utcnow() >= issued + auth.WINDOW:
        return 'expired'
    # Includes failed setup/claim/permit/upload and cancelled attempts without a ref.
    # A later ordinary run can finish cleanup if the original cleanup never ran.
    query = urlencode({'event': 'schedule', 'head_sha': code,
        'created': issued.isoformat() + '..' + (issued + auth.WINDOW).isoformat(), 'per_page': 100})
    runs = auth.collection(auth.api('actions/workflows/lightweight-monitor.yml/runs?' + query), 'workflow_runs')
    for run in runs:
        auth.require(run.get('head_sha') == code and run.get('head_branch') == 'main'
            and (run.get('head_repository') or {}).get('full_name') == auth.REPO
            and run.get('event') == 'schedule' and run.get('path') == auth.PATH
            and issued <= auth.stamp(run.get('created_at')) < issued + auth.WINDOW,
            'cleanup_history_invalid')
        jobs = auth.collection(auth.api(f'actions/runs/{run["id"]}/jobs?filter=all&per_page=100'), 'jobs')
        claims = [j for j in jobs if j.get('name') == auth.JOB]
        auth.require(len(claims) <= 1, 'cleanup_claim_ambiguous')
        for job in claims:
            auth.require(job.get('run_id') == run['id']
                and job.get('run_attempt') == run.get('run_attempt'), 'cleanup_claim_owner_invalid')
            if job.get('status') == 'completed' and job.get('conclusion') != 'skipped':
                steps = [s for s in job.get('steps', []) if s.get('name') == auth.STEP + identifier]
                if len(steps) == 1:
                    # Never turn OFF an observation still in its monitor job.
                    monitors = [j for j in jobs if j.get('name') == 'monitor']
                    auth.require(len(monitors) == 1 and monitors[0].get('run_id') == run['id']
                        and monitors[0].get('run_attempt') == run.get('run_attempt'), 'cleanup_monitor_ambiguous')
                    if monitors[0].get('status') == 'completed':
                        return 'attempt_finished'
    return 'pending'


def cleanup():
    owner()
    rows = snapshot()
    if value(rows, AUTHORIZATION) is None:
        auth.require(value(rows, CAPTURE) in (None, '0') and value(rows, EVENT_IDS) is None,
                     'cleanup_unbound_configuration')
        return 'already_off'
    identifier = os.environ.get(AUTHORIZATION, '')
    auth.require(value(rows, AUTHORIZATION) == identifier, 'cleanup_authorization_changed')
    match = re.fullmatch(r'(\d{8}T\d{6}Z)\.([a-f0-9]{40})\.([a-f0-9]{32})', identifier)
    auth.require(match, 'cleanup_authorization_invalid')
    issued = datetime.strptime(match[1], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    auth.require(issued <= utcnow(), 'cleanup_authorization_future')
    auth.require(value(rows, CAPTURE) in (None, '0', '1'), 'cleanup_capture_invalid')
    allowlist = value(rows, EVENT_IDS)
    auth.require(allowlist is None or json.loads(allowlist) == [auth.EVENT], 'cleanup_event_scope_changed')
    why = reason(identifier, issued, match[2])
    if why == 'pending':
        return why
    # GitHub variables have no atomic multi-variable CAS. Refuse observed changes;
    # operators MUST NOT replace/renew an ID while its cleanup is outstanding.
    auth.require(snapshot() == rows, 'cleanup_configuration_changed')
    if value(rows, CAPTURE) == '1':
        variable(CAPTURE, method='PATCH', value='0')
    current = snapshot()
    auth.require(value(current, CAPTURE) in (None, '0')
        and current[AUTHORIZATION] == rows[AUTHORIZATION] and current[EVENT_IDS] == rows[EVENT_IDS],
        'cleanup_OFF_or_ownership_unconfirmed')
    if current[EVENT_IDS] is not None:
        variable(EVENT_IDS, method='DELETE')
    current = snapshot()
    auth.require(value(current, CAPTURE) in (None, '0') and current[EVENT_IDS] is None
        and current[AUTHORIZATION] == rows[AUTHORIZATION], 'cleanup_ownership_changed')
    # Authorization is removed LAST, so partial cleanup is safely resumable.
    variable(AUTHORIZATION, method='DELETE')
    final = snapshot()
    auth.require(value(final, CAPTURE) in (None, '0') and final[AUTHORIZATION] is None
        and final[EVENT_IDS] is None, 'cleanup_OFF_not_confirmed')
    return 'off_' + why


def main():
    try:
        print('prospective_cleanup=' + cleanup())
    except (auth.AuthorizationError, OSError, ValueError, KeyError, TypeError, AttributeError):
        print('prospective_cleanup=HOLD; OFF not confirmed; no activation or claim retry')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
