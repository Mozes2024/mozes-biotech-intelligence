from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
import json
import pytest
from mozes import db, live_monitor as monitor, discovery
from mozes.alert_explanation import _optional_ai
from mozes.app_server import Handler
from mozes.primary_feeds import poll_fda_feeds
from mozes.program_identity import register, resolve
from mozes.event_coverage import matrix


def study(status='RECRUITING'):
    return {'protocolSection':{'identificationModule':{'nctId':'NCT01234567'},
            'statusModule':{'overallStatus':status}}}


def test_failed_change_write_does_not_advance_baseline(monkeypatch):
    c=db.connect(':memory:'); monitor.monitor_study(c,study())
    original=monitor.record_change
    monkeypatch.setattr(monitor,'record_change',lambda *a,**k: (_ for _ in ()).throw(RuntimeError('disk')))
    with pytest.raises(RuntimeError):monitor.monitor_study(c,study('TERMINATED'))
    monkeypatch.setattr(monitor,'record_change',original)
    assert len(monitor.monitor_study(c,study('TERMINATED')))==1
    assert monitor.monitor_study(c,study('TERMINATED'))==[]
    assert c.execute('SELECT COUNT(*) FROM change_events').fetchone()[0]==1


def test_partial_study_is_rejected_without_mutation():
    c=db.connect(':memory:');monitor.monitor_study(c,study())
    with pytest.raises(ValueError):monitor.monitor_study(c,{'protocolSection':{'identificationModule':{'nctId':'NCT01234567'}}})
    assert c.execute('SELECT COUNT(*) FROM trial_record_versions').fetchone()[0]==1


@pytest.mark.parametrize('payload',[{}, {'error':'upstream'}, {'studies':None}])
def test_unknown_search_envelope_is_not_complete(payload):
    with pytest.raises(ValueError):discovery.discover(fetcher=lambda _:payload)


def test_valid_empty_search():
    rows,meta=discovery.discover(fetcher=lambda _:{'totalCount':0},return_metadata=True)
    assert not rows and meta['complete']


def test_protocol_diff_has_both_values_and_hash():
    c=db.connect(':memory:');a=study();monitor.monitor_study(c,a)
    b=deepcopy(a);b['protocolSection']['outcomesModule']={'primaryOutcomes':[{'measure':'survival'}]}
    monitor.monitor_study(c,b)
    r=c.execute("SELECT * FROM change_events WHERE change_type='ctgov_primary_outcomes_changed'").fetchone()
    assert json.loads(r['previous_value'])==[]
    assert json.loads(r['new_value'])==[{'measure':'survival'}] and r['source_hash']
    assert r['verification_state']=='investigation_only'


def test_ai_draft_cannot_replace_canonical_explanation(monkeypatch):
    monkeypatch.setenv('MOZES_ALERT_AI','1')
    provider=SimpleNamespace(name='stub',complete=lambda _:json.dumps({'summary_he':'The trial met its endpoint.','evidence_ids':['source-1']}))
    result=_optional_ai({'summary_he':'Failed endpoint','missing_he':[],
        'evidence':[{'id':'source-1','quote':'The trial did not meet its endpoint.'}]},provider)
    assert result['summary_he']=='Failed endpoint' and result['ai_status']=='review_required'


def test_fda_cross_feed_and_legacy_dedup():
    c=db.connect(':memory:')
    xml=b'<rss><channel><item><title>FDA approves an oncology therapy</title><link>https://www.fda.gov/news-events/test</link><pubDate>Sat, 14 Nov 2026 12:00:00 GMT</pubDate></item></channel></rss>'
    for _ in range(2):poll_fda_feeds(c,fetch=lambda _:xml,now=datetime(2026,11,14,13,tzinfo=timezone.utc),resolve_headline=lambda *_:None)
    assert c.execute('SELECT COUNT(*) FROM change_events').fetchone()[0]==1


@pytest.mark.parametrize('origin,ctype,host',[
    ('https://foreign.invalid','application/json','127.0.0.1:8000'),
    ('http://127.0.0.1:8000','text/plain','127.0.0.1:8000'),
    ('http://foreign.invalid:8000','application/json','foreign.invalid:8000')])
def test_mutation_boundary_rejects_cross_origin(origin,ctype,host):
    h=Handler.__new__(Handler);h.path='/api/refresh'
    h.server=SimpleNamespace(server_port=8000)
    h.headers={'Origin':origin,'Content-Type':ctype,'Host':host}
    codes=[];h._send_json=lambda value,status=200:codes.append(status)
    h.do_POST();assert codes==[403]


def test_program_aliases_preserve_time_and_shared_license():
    c=db.connect(':memory:')
    for ticker in ['AAA','BBB']:
        register(c,program_id='program-1',alias='molecule-X',ticker=ticker,relationship='partner',
            valid_from='2026-01-01',source_url='https://example.org/release',observed_at='2026-02-01T00:00:00+00:00')
    assert resolve(c,'molecule-X',as_of='2026-01-15')==[]
    assert {r['ticker'] for r in resolve(c,'MOLECULE-x',as_of='2026-03-01')}=={'AAA','BBB'}


def test_coverage_does_not_upgrade_stale_alternate_to_verification():
    rows=matrix([{'id':'e','ticker':'AAA'}],[{'source':'official_feed:AAA',
        'last_success_at':'2026-01-01T00:00:00+00:00','covered_by':'wire'}],
        now=datetime(2026,11,14,tzinfo=timezone.utc))
    assert rows[0]['needs_collection'] and not rows[0]['sources'][0]['verifies_event']


def test_newsroom_baselines_then_records_only_new_same_host_release():
    from mozes.news_signals import _poll_reviewed_newsroom
    c=db.connect(':memory:');now=datetime(2026,11,14,tzinfo=timezone.utc)
    entry={'newsroom_url':'https://ir.example.org/press-releases'}
    def page(slug):
        return '<a href="/news-releases/'+slug+'">Company reports clinical trial endpoint results</a>'
    old=page('old-clinical-trial-endpoint-results')
    assert _poll_reviewed_newsroom(c,'AAA',entry,fetch=lambda _:old,now=now)['signals_seen']==0
    new=old+page('new-clinical-trial-endpoint-results')
    fetch=lambda url:new if url==entry['newsroom_url'] else '{"datePublished":"2026-11-13T15:00:00Z"}'
    assert _poll_reviewed_newsroom(c,'AAA',entry,fetch=fetch,now=now)['signals_seen']==1
    assert _poll_reviewed_newsroom(c,'AAA',entry,fetch=fetch,now=now)['signals_seen']==0
    assert c.execute('SELECT COUNT(*) FROM change_events').fetchone()[0]==1


def test_zero_ctgov_budget_does_not_fetch(monkeypatch):
    c=db.connect(':memory:')
    monkeypatch.setattr(monitor.ctgov,'fetch_study',lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')))
    # Existing candidates prevent the separate initial discovery path.
    from mozes.discovery import store_candidates,study_to_candidate
    s=study();s['protocolSection']['statusModule']['primaryCompletionDateStruct']={'date':date.today().isoformat()}
    store_candidates(c,[study_to_candidate(s)])
    result=monitor.run_monitor(c,audit=False,sec=False,ctgov_budget_seconds=0)
    assert result['status']=='PARTIAL'


@pytest.mark.parametrize('article',[
    '{"datePublished":"2024-01-01T00:00:00Z"}',
    '{"datePublished":"2027-01-01T00:00:00Z"}',
    '<article>missing publication evidence</article>'])
def test_old_or_unproven_newsroom_additions_never_alert(article):
    from mozes.news_signals import _poll_reviewed_newsroom
    c=db.connect(':memory:');now=datetime(2026,11,14,tzinfo=timezone.utc)
    entry={'newsroom_url':'https://ir.example.org/press-releases'}
    old='<a href="/news-releases/old-clinical-trial-endpoint-results">Company reports clinical trial endpoint results</a>'
    _poll_reviewed_newsroom(c,'AAA',entry,fetch=lambda _:old,now=now)
    page=old+'<a href="/news-releases/new-clinical-trial-endpoint-results">Company reports additional clinical trial endpoint results</a>'
    result=_poll_reviewed_newsroom(c,'AAA',entry,fetch=lambda u:page if u==entry['newsroom_url'] else article,now=now)
    assert result['signals_seen']==0
    assert c.execute('SELECT COUNT(*) FROM change_events').fetchone()[0]==0
