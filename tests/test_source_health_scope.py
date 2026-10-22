import json
from mozes import db
from mozes.live_monitor import observe
from mozes.payload_v3 import _health, build


def test_missing_ir_does_not_count_as_a_successful_source_scan(tmp_path):
    conn = db.connect(tmp_path / 'health.db')
    observe(conn, 'official_feed:UNCONFIG', {'status':'no_official_site', 'checked_at':'2026-10-20T08:00:00Z'})
    value = json.loads(conn.execute('SELECT value_json FROM monitor_observations').fetchone()[0])
    assert value['last_success_at'] is None
    observe(conn, 'official_feed:SLS', {'status':'no_rss_found', 'checked_at':'2026-10-20T08:00:00Z'})
    rows = {r['source']:r for r in _health(conn, [], None, None)['source_freshness']}
    assert rows['official_feed:UNCONFIG']['in_scope'] is False
    assert rows['official_feed:UNCONFIG']['monitoring_scope'] == 'unconfigured_ir'
    assert rows['official_feed:SLS']['in_scope'] is True
    assert rows['official_feed:SLS']['status'] == 'PARTIAL'
    assert rows['official_feed:SLS']['last_success_at'] is None


def test_source_failure_and_recovery_preserve_history_without_current_failure(tmp_path):
    conn = db.connect(tmp_path / 'health.db')
    observe(conn, 'wire_feed:wire', {'status':'fetch_error', 'error':'HTTP 403', 'checked_at':'2026-10-19T08:00:00Z'})
    failed = _health(conn, [], None, None)['source_freshness'][0]
    assert failed['status'] == 'FAILED' and failed['last_error'] == 'HTTP 403'
    observe(conn, 'wire_feed:wire', {'status':'OK', 'checked_at':'2026-10-20T08:00:00Z'})
    recovered = _health(conn, [], None, None)['source_freshness'][0]
    assert recovered['status'] == 'OK' and recovered['consecutive_failures'] == 0
    assert recovered['last_error_at'] == '2026-10-19T08:00:00Z'
    assert recovered['last_success_at'] == '2026-10-20T08:00:00Z'


def test_retired_feed_and_incomplete_legacy_success_remain_visible_but_not_current(tmp_path):
    conn = db.connect(tmp_path / 'legacy.db')
    observe(conn, 'wire_feed:retired', {'status':'fetch_error', 'checked_at':'2026-10-01T08:00:00Z'})
    observe(conn, 'official_feed:KOD', {'status':'fetch_error', 'site':'https://kodiak.gcs-web.com/', 'checked_at':'2026-10-01T08:00:00Z'})
    observe(conn, 'official_feed:SLS', {'status':'no_rss_found', 'checked_at':'2026-10-20T08:00:00Z'})
    row = conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:SLS'").fetchone()
    value = json.loads(row[0])
    value['last_success_at'] = value['checked_at']
    conn.execute("UPDATE monitor_observations SET value_json=? WHERE observation_key='official_feed:SLS'", (json.dumps(value),))
    rows = {r['source']:r for r in _health(conn, [], None, None)['source_freshness']}
    assert rows['wire_feed:retired']['monitoring_scope'] == 'retired_feed'
    assert rows['wire_feed:retired']['in_scope'] is False
    assert rows['wire_feed:retired']['status'] == 'FAILED'
    assert rows['official_feed:KOD']['in_scope'] is False
    assert rows['official_feed:KOD']['status'] == 'FAILED'
    assert rows['official_feed:SLS']['last_success_at'] is None


def test_backlog_audit_only_issues_fixed_select():
    from scripts.audit_edge_backlog import inspect, SQL, URL
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,size): return b'{"success":true,"result":[{"success":true,"results":[]}]}'
    def opener(request, **kwargs):
        assert request.full_url == URL
        assert json.loads(request.data) == {'sql': SQL}
        assert SQL.startswith('SELECT ') and ';' not in SQL
        assert not any(word in SQL.upper() for word in ('UPDATE ', 'INSERT ', 'DELETE ', 'DROP ', 'ALTER '))
        return Response()
    assert inspect(token='test', opener=opener)['events'] == []


def test_bounded_ir_coverage_separates_configured_unobserved_from_watch_without_sites(tmp_path):
    from datetime import date
    conn = db.connect(tmp_path / 'coverage.db')
    db.upsert_watch(conn, 'TYRA')
    db.upsert_watch(conn, 'ZZZZ')
    coverage = build(conn, date(2026,10,20), consumer_only=True)['ir_coverage']
    assert coverage['configured_count'] == 41
    assert coverage['priority_count'] == 40
    assert coverage['rotating_count'] == 1
    assert coverage['rotating_per_cycle'] == 8
    assert 'TYRA' in coverage['configured_without_observation']
    assert coverage['watch_configured_count'] == 1
    assert coverage['watch_without_configured_site'] == 1
    assert coverage['watch_without_observation'] == 2


def _sec_streams(conn, status='active'):
    for form in ('8-K', '6-K'):
        observe(conn, 'sec_latest:' + form, {'status': status, 'checked_at': '2026-10-22T08:00:00Z'})


def test_blocked_ir_page_is_marked_covered_while_sec_streams_are_healthy(tmp_path):
    conn = db.connect(tmp_path / 'covered.db')
    _sec_streams(conn)
    observe(conn, 'official_feed:AMGN', {'status': 'fetch_error', 'error': 'HTTP 403', 'checked_at': '2026-10-22T08:00:00Z'})
    rows = {r['source']: r for r in _health(conn, [], None, None)['source_freshness']}
    assert rows['official_feed:AMGN']['status'] == 'FAILED'  # raw status is never hidden
    assert rows['official_feed:AMGN']['covered_by'] == 'sec_8k_6k'
    assert 'covered_by' not in rows['sec_latest:8-K']


def test_no_alternate_coverage_when_sec_stream_is_failing_or_missing(tmp_path):
    conn = db.connect(tmp_path / 'uncovered.db')
    observe(conn, 'official_feed:AMGN', {'status': 'fetch_error', 'checked_at': '2026-10-22T08:00:00Z'})
    rows = {r['source']: r for r in _health(conn, [], None, None)['source_freshness']}
    assert 'covered_by' not in rows['official_feed:AMGN']  # no SEC observations at all
    observe(conn, 'sec_latest:8-K', {'status': 'active', 'checked_at': '2026-10-22T08:00:00Z'})
    observe(conn, 'sec_latest:6-K', {'status': 'fetch_error', 'checked_at': '2026-10-22T08:00:00Z'})
    rows = {r['source']: r for r in _health(conn, [], None, None)['source_freshness']}
    assert 'covered_by' not in rows['official_feed:AMGN']  # one stream down


def test_failing_wire_or_sec_feed_is_never_marked_covered(tmp_path):
    conn = db.connect(tmp_path / 'primary.db')
    _sec_streams(conn)
    observe(conn, 'wire_feed:businesswire_health', {'status': 'fetch_error', 'checked_at': '2026-10-22T08:00:00Z'})
    rows = {r['source']: r for r in _health(conn, [], None, None)['source_freshness']}
    assert 'covered_by' not in rows['wire_feed:businesswire_health']


def test_registry_has_no_known_dead_ir_hosts():
    from mozes.ir_registry import load_ir_registry
    dead = {'ir.acadia-pharm.com', 'investors.argenx.com', 'ir.axsome.com', 'investors.editasmedicine.com',
            'ir.krystalbiotech.com', 'ir.legendbiotech.com'}
    from urllib.parse import urlsplit
    hosts = {urlsplit(r['site']).hostname for r in load_ir_registry().values()}
    assert not hosts & dead
    assert load_ir_registry()['ACAD']['feed_url'].startswith('https://ir.acadia.com/')


def test_connect_sets_busy_timeout(tmp_path):
    conn = db.connect(tmp_path / 'wal.db')
    assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == 30000
    # WAL is deliberately not enabled: the monitor copies the single .db file as its checkpoint.
    assert conn.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal'


def test_pyproject_requires_python_matches_tomllib_use():
    import pathlib
    text = pathlib.Path('pyproject.toml').read_text()
    assert 'requires-python = ">=3.11"' in text
