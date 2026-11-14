"""Complete discovery is necessary, not permission to weaken producer evidence."""
import copy
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from mozes import monitor_recovery as m

NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)
OLD = '2026-11-10T00:00:00Z'
NEW = '2026-11-12T00:00:00Z'


def run(i, **kw):
    return dict(id=i, status='completed', conclusion='failure', updated_at=OLD,
                created_at=OLD, name='lightweight-live-monitor', head_branch='main',
                head_repository={'full_name': 'Owner/Repo'}, run_attempt=1, **kw)


class API:
    def __init__(self, rows, change=None, jobs=None):
        self.rows = rows
        self.change = change
        self.jobs = jobs
        self.calls = []
        self.scans = 0

    def __call__(self, *args, **kwargs):
        path = args[1]
        self.calls.append((path, kwargs))
        if '/workflows/' not in path:
            if self.jobs is not None:
                return json.dumps(self.jobs)
            return json.dumps({'total_count': 1, 'jobs': [{'name': 'monitor', 'steps': [
                {'name': n, 'conclusion': 'skipped'} for n in
                (*m.DELIVERY_STEPS, 'Enrich the correlated Edge event')]}]})
        query = parse_qs(urlsplit(path).query)
        assert set(query) == {'per_page', 'page'}  # No filtered 1000-result cap.
        page = int(query['page'][0])
        if page == 1:
            self.scans += 1
        rows = copy.deepcopy(self.rows)
        result = {'total_count': len(rows), 'workflow_runs': rows[(page-1)*100:page*100]}
        if self.change:
            self.change(self, page, result)
        return json.dumps(result)


def audit(api):
    return m.audit_producers('Owner/Repo', datetime(2026, 11, 11, tzinfo=timezone.utc),
                             api, now=NOW, full_history=True)


@pytest.mark.parametrize('count', [0, 1, 100, 101, 5100, 5914])
def test_complete_terminal_page_and_recheck(count):
    api = API([run(i+1) for i in range(count)])
    assert audit(api)['audited_producers'] == []
    assert api.scans == 2
    pages = [int(parse_qs(urlsplit(p).query)['page'][0]) for p, _ in api.calls]
    assert len(pages) == 2 * (count // 100 + 1)
    assert max(pages) == count // 100 + 1
    assert all(0 < k['timeout'] <= 30 for _, k in api.calls)


def test_late_updated_old_created_run_beyond_previous_bound_blocks_delivery():
    rows = [run(i+1) for i in range(5101)]
    rows[-1]['updated_at'] = NEW
    api = API(rows, jobs={'total_count': 1, 'jobs': [{'steps': [
        {'name': n, 'conclusion': 'success'} for n in m.DELIVERY_STEPS]}]})
    with pytest.raises(RuntimeError, match='changed/delivered'):
        audit(api)


@pytest.mark.parametrize('case', ['count', 'short', 'duplicate', 'missing', 'wrong_type',
                                  'bad_id', 'bad_time', 'naive_time', 'missing_time', 'extra'])
def test_malformed_or_incomplete_page_fails(case):
    def change(api, page, value):
        if page != 2:
            return
        if case == 'count': value['total_count'] += 1
        if case == 'short': value['workflow_runs'].pop()
        if case == 'duplicate': value['workflow_runs'][0] = run(1)
        if case == 'missing': del value['workflow_runs']
        if case == 'wrong_type': value['total_count'] = '101'
        if case == 'bad_id': value['workflow_runs'][0]['id'] = True
        if case == 'bad_time': value['workflow_runs'][0]['updated_at'] = 'bad'
        if case == 'naive_time': value['workflow_runs'][0]['updated_at'] = '2026-11-10T00:00:00'
        if case == 'missing_time': del value['workflow_runs'][0]['updated_at']
        if case == 'extra': value['workflow_runs'].append(run(999))
    with pytest.raises(RuntimeError):
        audit(API([run(i+1) for i in range(101)], change=change))


@pytest.mark.parametrize('case', ['new_run', 'removed_run', 'updated', 'same_count_replacement',
                                  'conclusion', 'attempt', 'old_run_update'])
def test_metadata_change_after_jobs_fails_even_with_stable_count(case):
    rows = [run(1), run(2)]
    rows[0]['updated_at'] = NEW
    def change(api, page, value):
        if api.scans < 2: return
        if case == 'new_run':
            value['total_count'] += 1
            value['workflow_runs'].append(run(3))
        elif case == 'removed_run':
            value['total_count'] -= 1
            value['workflow_runs'].pop()
        elif case == 'same_count_replacement': value['workflow_runs'][0]['id'] = 3
        else:
            row = value['workflow_runs'][1 if case == 'old_run_update' else 0]
            row[{'updated': 'updated_at', 'old_run_update': 'updated_at',
                 'conclusion': 'conclusion', 'attempt': 'run_attempt'}[case]] = (
                     2 if case == 'attempt' else 'success' if case == 'conclusion' else '2026-11-12T01:00:00Z')
    with pytest.raises(RuntimeError, match='changed during audit'):
        audit(API(rows, change=change))


def test_inventory_change_mid_scan_fails():
    def change(api, page, value):
        if page == 2: value['total_count'] += 1
    with pytest.raises(RuntimeError, match='changed producer inventory'):
        audit(API([run(i+1) for i in range(101)], change=change))


def test_exact_full_last_page_requires_empty_next_page():
    def change(api, page, value):
        if page == 2: value['workflow_runs'] = [run(999)]
    with pytest.raises(RuntimeError, match='inventory page'):
        audit(API([run(i+1) for i in range(100)], change=change))


def test_resource_bound_does_not_claim_complete(monkeypatch):
    monkeypatch.setattr(m, 'PRODUCER_INVENTORY_MAX_PAGES', 1)
    with pytest.raises(RuntimeError, match='resource bound'):
        audit(API([run(i+1) for i in range(101)]))


def test_time_budget_does_not_claim_complete(monkeypatch):
    clock = [0]
    monkeypatch.setattr(m.time, 'monotonic', lambda: clock[0])
    api = API([])
    def gh(*a, **kw):
        result = api(*a, **kw)
        clock[0] += m.PRODUCER_AUDIT_SECONDS
        return result
    with pytest.raises(RuntimeError, match='time budget'):
        audit(gh)


@pytest.mark.parametrize('exception', [TimeoutError, OSError, json.JSONDecodeError])
def test_provider_errors_are_not_partial_success(exception):
    def gh(*a, **kw):
        if exception is json.JSONDecodeError: raise exception('bad', 'x', 0)
        raise exception('unavailable')
    with pytest.raises(exception):
        audit(gh)


@pytest.mark.parametrize('status', ['in_progress', 'queued', 'pending'])
def test_active_or_started_pending_producer_still_blocks(status):
    row = run(1); row.update(status=status, updated_at=NEW)
    with pytest.raises(RuntimeError):
        audit(API([row], jobs={'total_count': 1, 'jobs': [
            {'name': 'monitor', 'status': 'in_progress', 'started_at': NEW}]}))


def test_zero_job_startup_failure_still_blocks():
    row = run(1); row.update(conclusion='startup_failure', updated_at=NEW)
    with pytest.raises(RuntimeError, match='incomplete or unexpected producer jobs'):
        audit(API([row], jobs={'total_count': 0, 'jobs': []}))


def test_rerun_still_blocks():
    row = run(1); row.update(run_attempt=2, updated_at=NEW)
    with pytest.raises(RuntimeError, match='reruns'):
        audit(API([row]))


def test_relevant_producer_cap_unchanged():
    rows = [run(i+1) for i in range(501)]
    for row in rows: row['updated_at'] = NEW
    with pytest.raises(RuntimeError, match='500 relevant producers'):
        audit(API(rows))


def test_only_hash_bound_source_and_current_run_are_excluded(monkeypatch):
    rows = [run(1), run(2), run(3)]
    for row in rows: row['updated_at'] = NEW
    monkeypatch.setenv('GITHUB_RUN_ID', '2')
    assert m.audit_producers('Owner/Repo', datetime(2026, 11, 11, tzinfo=timezone.utc),
        API(rows), now=NOW, full_history=True, source_run_id='1')['audited_producers'] == ['3']


def test_started_job_is_not_hidden_by_queued_label():
    row = run(1); row.update(status='queued', updated_at=NEW)
    with pytest.raises(RuntimeError, match='pending producer'):
        audit(API([row], jobs={'total_count': 1, 'jobs': [{'status': 'queued',
            'started_at': NEW, 'steps': []}]}))


def test_incomplete_job_collection_still_blocks():
    row = run(1); row['updated_at'] = NEW
    with pytest.raises(RuntimeError, match='incomplete or unexpected producer jobs'):
        audit(API([row], jobs={'total_count': 101, 'jobs': []}))
