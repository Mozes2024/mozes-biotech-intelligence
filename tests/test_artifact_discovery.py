"""Artifact discovery completeness cannot be inferred from a first-N match count."""
import copy
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from test_restore_no_rewind import module


def artifact(i, **kw):
    return dict(id=i, name='mozes-publish-request', created_at='2026-11-11T00:00:00Z',
                workflow_run={'id': i, 'head_branch': 'main'}, **kw)


class API:
    def __init__(self, rows, change=None):
        self.rows, self.change, self.calls, self.scans = rows, change, [], 0

    def __call__(self, *args, **kwargs):
        query = parse_qs(urlsplit(args[1]).query)
        assert set(query) == {'page', 'per_page'}
        assert 0 < kwargs['timeout'] <= 30
        number = int(query['page'][0]); self.calls.append(number)
        if number == 1:
            self.scans += 1
        result = {'total_count': len(self.rows), 'artifacts': copy.deepcopy(self.rows[(number-1)*100:number*100])}
        if self.change:
            self.change(self, number, result)
        return json.dumps(result)


def discover(monkeypatch, api):
    script = module()
    monkeypatch.setenv('GITHUB_REPOSITORY', 'Owner/Repo')
    monkeypatch.setattr(script, 'gh', api)
    return script, script._list_success_runs


@pytest.mark.parametrize('count', [0, 1, 100, 101, 500, 501, 2269])
def test_full_unfiltered_terminal_inventory(monkeypatch, count):
    api = API([artifact(i+1) for i in range(count)])
    _, call = discover(monkeypatch, api)
    assert call() == []
    assert api.scans == 2
    assert len(api.calls) == 2*(count//100+1)
    assert max(api.calls) == count//100+1


def test_unsorted_late_newest_expired_checkpoint_not_hidden_by_first_twenty(monkeypatch):
    rows = [artifact(i+1) for i in range(601)]
    for a in rows[:20]:
        a['name'] = 'mozes-live-monitor'
    rows[-1].update(name='mozes-live-monitor', created_at='2026-11-12T00:00:00+00:00', expired=True)
    _, call = discover(monkeypatch, API(rows))
    result = call()
    assert len(result) == 21
    assert result[0] == {'databaseId': 601, 'createdAt': '2026-11-12T00:00:00+00:00'}


def test_timestamp_sort_uses_offsets_not_lexical_order(monkeypatch):
    rows = [artifact(1), artifact(2)]
    rows[0].update(name='mozes-live-monitor',created_at='2026-11-12T01:00:00+02:00')
    rows[1].update(name='mozes-live-monitor',created_at='2026-11-12T00:00:00Z')
    _, call = discover(monkeypatch, API(rows))
    assert [r['databaseId'] for r in call()] == [2,1]


@pytest.mark.parametrize('mutation', [
    lambda r: r.pop('total_count'),
    lambda r: r.update(total_count=True),
    lambda r: r.update(total_count=-1),
    lambda r: r.update(total_count=202),
    lambda r: r.update(artifacts=r['artifacts'][:-1]),
    lambda r: r['artifacts'][0].update(id=0),
    lambda r: r['artifacts'][0].update(id=True),
    lambda r: r['artifacts'][0].update(name=None),
])
def test_bad_counts_pages_and_metadata_fail_closed(monkeypatch, mutation):
    rows = [artifact(i+1) for i in range(201)]
    api = API(rows, lambda api,n,r: mutation(r) if n==1 else None)
    _, call = discover(monkeypatch, api)
    with pytest.raises(RuntimeError):call()


@pytest.mark.parametrize('field,value', [('created_at',None),('created_at','2026-11-12T00:00:00'),
    ('created_at','bogus'),('workflow_run',{}),('workflow_run',{'id':'1','head_branch':'main'}),
    ('workflow_run',{'id':1,'head_branch':None})])
def test_monitor_provenance_must_be_qualified(monkeypatch, field,value):
    row=artifact(1);row.update(name='mozes-live-monitor');row[field]=value
    _,call=discover(monkeypatch,API([row]))
    with pytest.raises(RuntimeError,match='provenance'):call()


def test_duplicate_ids_fail(monkeypatch):
    _,call=discover(monkeypatch,API([artifact(1),artifact(1)]))
    with pytest.raises(RuntimeError,match='duplicate'):call()


def test_same_count_changed_metadata_second_inventory_fails(monkeypatch):
    def change(api,n,r):
        if api.scans==2 and n==1:r['artifacts'][0]['expired']=True
    _,call=discover(monkeypatch,API([artifact(1)],change))
    with pytest.raises(RuntimeError,match='changed during'):call()


def test_budget_and_resource_bounds_fail_without_candidates(monkeypatch):
    script,call=discover(monkeypatch,API([artifact(1)]))
    monkeypatch.setattr(script,'ARTIFACT_DISCOVERY_SECONDS',0)
    with pytest.raises(RuntimeError,match='time budget'):call()
    monkeypatch.setattr(script,'ARTIFACT_DISCOVERY_SECONDS',120)
    monkeypatch.setattr(script,'ARTIFACT_DISCOVERY_MAX_PAGES',0)
    with pytest.raises(RuntimeError,match='resource bound'):call()
