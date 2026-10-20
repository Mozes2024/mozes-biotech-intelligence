from pathlib import Path
import ast
import re

import pytest

ROOT=Path(__file__).resolve().parents[1]


def workflow_sections(source, wanted):
    """Read the workflow's mapping sections, independent of quoting/indent width.

    Deliberately not a general YAML loader: unsupported contract shapes fail
    assertions rather than being silently treated as equivalent permissions.
    """
    lines = source.splitlines()
    parents = []
    pattern = re.compile(r'''^( *)(?:- )?(["']?)([\w-]+)\2:\s*(.*?)\s*$''')
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        indent, _, key, value = match.groups()
        depth = len(indent)
        while parents and parents[-1][0] >= depth:
            parents.pop()
        value = value.split(' #', 1)[0].strip()
        if key == wanted:
            end = index+1
            while end < len(lines):
                following = lines[end]
                if following.strip() and not following.lstrip().startswith('#') and len(following)-len(following.lstrip()) <= depth:
                    break
                end += 1
            yield tuple(name for _, name in parents), value, lines[index+1:end]
        if not value:
            parents.append((depth, key))


def workflow_fields(value, lines):
    def scalar(raw):
        return raw.strip().strip("\"'")
    if value.startswith('{') and value.endswith('}'):
        return {scalar(k): scalar(v) for k, v in
                (item.split(':', 1) for item in value[1:-1].split(',') if item.strip())}
    assert not value, f'Unsupported contract mapping: {value}'
    rows = [line for line in lines if line.strip() and not line.lstrip().startswith('#')]
    if not rows:
        return {}
    depth = min(len(line)-len(line.lstrip()) for line in rows)
    fields = {}
    for index, line in enumerate(rows):
        if len(line)-len(line.lstrip()) != depth:
            continue
        key, raw = line.strip().split(':', 1)
        raw = raw.split(' #', 1)[0].strip()
        fields[scalar(key)] = ('\n'.join(item.strip() for item in rows[index+1:]
            if len(item)-len(item.lstrip()) > depth) if raw == '|' else scalar(raw))
    return fields


def permission_contract(source, expected):
    sections = list(workflow_sections(source, 'permissions'))
    actual = {context: workflow_fields(value, body) for context, value, body in sections}
    assert len(actual) == len(sections), 'Duplicate permission mappings'
    assert actual == expected


def test_workflow_permission_boundaries():
    read = {'contents': 'read'}
    producer = {**read, 'actions': 'write'}
    contracts = {
        'ci.yml': {(): read},
        'pages.yml': {(): {**read, 'actions': 'read', 'pages': 'write', 'id-token': 'write'}},
        'deep-monitor.yml': {(): producer},
        'nightly.yml': {(): read},
        'lightweight-monitor.yml': {(): producer,
            ('jobs', 'incident_eligibility'): {'contents': 'write', 'actions': 'read'},
            ('jobs', 'prospective_claim'): {'contents': 'write', 'actions': 'read'},
            ('jobs', 'monitor'): producer,
            ('jobs', 'prospective_cleanup'): {**read, 'actions': 'read'}},
    }
    for name, expected in contracts.items():
        permission_contract((ROOT/'.github/workflows'/name).read_text(), expected)


def test_shared_lineage_lock_cannot_cancel_or_replace_queued_owners():
    for name in ('lightweight-monitor.yml', 'pages.yml'):
        sections = list(workflow_sections((ROOT/'.github/workflows'/name).read_text(), 'concurrency'))
        assert len(sections) == 1
        _, value, body = sections[0]
        assert workflow_fields(value, body) == {
            'group': 'mozes-hot-monitor', 'cancel-in-progress': 'false', 'queue': 'max'}


def test_workflow_trigger_boundaries_and_deep_suspension():
    events = {'ci.yml': {'push', 'pull_request', 'workflow_dispatch'},
              'pages.yml': {'workflow_run', 'workflow_dispatch'},
              'lightweight-monitor.yml': {'schedule', 'workflow_dispatch'},
              'deep-monitor.yml': {'workflow_dispatch'}, 'nightly.yml': {'workflow_dispatch'}}
    for name, allowed in events.items():
        source = (ROOT/'.github/workflows'/name).read_text()
        sections = list(workflow_sections(source, 'on'))
        assert len(sections) == 1
        _, value, body = sections[0]
        assert set(workflow_fields(value, body)) == allowed
    deep = (ROOT/'.github/workflows/deep-monitor.yml').read_text()
    assert ('jobs', 'monitor') in [context for context, value, _ in workflow_sections(deep, 'if')
                                  if value == '${{ false }}']
    hot = (ROOT/'.github/workflows/lightweight-monitor.yml').read_text()
    assert {value.strip("\"'") for _, value, _ in workflow_sections(hot, 'cron')} == {
        '7,22,37,52 * * * *', '20 0,12,18 * * *', '20 6 * * 0,2,4,6'}


def test_checkpoint_artifact_layout_and_publication_receipt_contract():
    hot = (ROOT/'.github/workflows/lightweight-monitor.yml').read_text()
    assert [value for _, value, _ in workflow_sections(hot, 'MOZES_DB_PATH')] == ['.monitor/mozes-live.db']
    uploads = [workflow_fields(value, body) for _, value, body in workflow_sections(hot, 'with')]
    checkpoint = [item for item in uploads if item.get('name') == 'mozes-live-monitor']
    assert len(checkpoint) == 1
    assert set(checkpoint[0]['path'].splitlines()) == {
        '.monitor/mozes-live.db', '.monitor/manifest.json', '.monitor/edge-checkpoint.json',
        '.monitor/recovery-proof.json', '.monitor/prospective-pilot.json'}
    assert checkpoint[0]['include-hidden-files'] == 'true'
    pages = (ROOT/'.github/workflows/pages.yml').read_text()
    published = [workflow_fields(value, body) for _, value, body in workflow_sections(pages, 'with')]
    assert any(item.get('name') == 'mozes-published-ledger' and
               item.get('path') == '/tmp/mozes-published/publish-receipt.json' for item in published)
    assert '--consumer-only' in pages and 'mozes.publication_control preflight' in pages
    assert pages.index('actions/deploy-pages@') < pages.index('name: mozes-published-ledger')


def test_production_secrets_stay_in_declared_environment_bindings():
    allowed = {'EDGE_SYNC_TOKEN', 'CLOUDFLARE_RECOVERY_READ_TOKEN', 'MOZES_NTFY_URL',
               'MOZES_WEBHOOK_URL', 'MOZES_SMTP_USER', 'MOZES_SMTP_PASSWORD',
               'MOZES_ALERT_EMAIL_TO', 'ANTHROPIC_API_KEY', 'MOZES_ALERT_FEED_TOKEN',
               'MOZES_PROSPECTIVE_CLEANUP_TOKEN'}
    for name in ('ci.yml', 'pages.yml', 'lightweight-monitor.yml', 'deep-monitor.yml'):
        source = (ROOT/'.github/workflows'/name).read_text()
        assert not re.search(r'toJSON\s*\(\s*secrets\s*\)|secrets\s*:\s*inherit', source, re.I)
        for line in source.splitlines():
            if 'secrets.' in line and not line.lstrip().startswith('#'):
                key = line.strip().split(':', 1)[0]
                sections = list(workflow_sections(source, key))
                assert any('env' in context and value == line.strip().split(':', 1)[1].strip()
                           for context, value, _ in sections)
                assert set(re.findall(r'secrets\.([A-Za-z_][A-Za-z_0-9]*)', line)) <= allowed


@pytest.mark.parametrize('source', [
    'permissions: write-all',
    'permissions: {contents: write}',
    'permissions:\n  contents: read\njobs:\n  monitor:\n    permissions:\n      contents: write',
])
def test_permission_contract_rejects_escalation(source):
    with pytest.raises(AssertionError):
        permission_contract(source, {(): {'contents': 'read'}})


def test_permission_contract_accepts_harmless_quotes_comments_and_indentation():
    permission_contract('"permissions":\n    "contents": "read" # least privilege\n',
                        {(): {'contents': 'read'}})
    permission_contract('permissions: {contents: read}', {(): {'contents': 'read'}})


@pytest.mark.parametrize('filename,old,new,contract', [
    ('pages.yml', 'cancel-in-progress: false', 'cancel-in-progress: true',
     test_shared_lineage_lock_cannot_cancel_or_replace_queued_owners),
    ('ci.yml', '  pull_request:', '  pull_request_target:',
     test_workflow_trigger_boundaries_and_deep_suspension),
    ('lightweight-monitor.yml', '.monitor/mozes-live.db', '.monitor/wrong.db',
     test_checkpoint_artifact_layout_and_publication_receipt_contract),
    ('ci.yml', 'jobs:', 'env:\n  LEAK: ${{ toJSON(secrets) }}\njobs:',
     test_production_secrets_stay_in_declared_environment_bindings),
])
def test_safety_contracts_reject_unsafe_workflow_mutations(tmp_path, monkeypatch, filename, old, new, contract):
    directory = tmp_path/'.github/workflows'
    directory.mkdir(parents=True)
    for source in (ROOT/'.github/workflows').glob('*.yml'):
        (directory/source.name).write_text(source.read_text())
    target = directory/filename
    original = target.read_text()
    assert old in original
    target.write_text(original.replace(old, new, 1))
    monkeypatch.setitem(globals(), 'ROOT', tmp_path)
    with pytest.raises(AssertionError):
        contract()


def test_pages_has_no_source_scan_or_duplicate_tests():
    source=(ROOT/'.github/workflows/pages.yml').read_text()
    for forbidden in ('mozes refresh-v2', 'mozes discover-v2', 'mozes monitor-live', 'pytest', 'mozes.live_prices', 'schedule:'):
        assert forbidden not in source
    assert 'mozes.pipeline_v2c export' in source
    assert "workflows: ['ci']" in source


def test_legacy_nightly_is_not_scheduled():
    assert 'schedule:' not in (ROOT/'.github/workflows/nightly.yml').read_text()


def test_one_python_ci_and_code_path_filters():
    source=(ROOT/'.github/workflows/ci.yml').read_text()
    assert 'matrix:' not in source
    assert 'paths:' in source and "'scripts/**'" in source
    assert "python-version: '3.11'" in source
    assert 'pytest -q' in source


def test_hot_and_deep_concurrency_are_separated():
    hot = (ROOT / '.github/workflows/lightweight-monitor.yml').read_text()
    deep = (ROOT / '.github/workflows/deep-monitor.yml').read_text()
    assert 'group: mozes-hot-monitor' in hot
    assert 'group: mozes-deep-monitor' in deep
    assert 'group: mozes-live-data-producer' not in hot
    assert 'group: mozes-live-data-producer' not in deep
    assert "steps.data.outputs.should_publish == 'true'" in hot
    assert 'timeout-minutes:' in hot
    assert 'monitor --deep' in deep


def test_priority_schedule_uses_short_monitor_path():
    source = (ROOT / '.github/workflows/lightweight-monitor.yml').read_text()
    assert "cron: '7,22,37,52 * * * *'" in source
    assert 'monitor --priority-only' in source
    assert 'group: mozes-hot-monitor' in source
    assert 'MOZES_SEC_HOT' in source
    assert 'MOZES_ALERT_DISPATCH' in source


def test_priority_pipeline_does_not_run_broad_enrichment(tmp_path, monkeypatch):
    from mozes import db, live_monitor, pipeline_v2c
    from mozes.intelligence_store import ensure_schema
    conn=db.connect(tmp_path/'priority.db')
    ensure_schema(conn)
    monkeypatch.setattr(pipeline_v2c,'reconcile_catalog',lambda _: {'changed': [], 'conflicts': []})
    called=[]
    def short_monitor(_conn, **kwargs):
        called.append(kwargs)
        return {'status': 'OK', 'changes': 0}
    monkeypatch.setattr(live_monitor,'run_monitor',short_monitor)
    result=pipeline_v2c.run_priority_pipeline(conn)
    assert result['status']=='OK'
    assert called==[{'audit': False, 'ctgov_diff': False, 'sec': True,
                     'news': True, 'priority_only': True}]


def test_hosted_monitor_checks_sec_filings():
    source=(ROOT/'mozes/pipeline_v2c.py').read_text()
    tree=ast.parse(source)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_pipeline')
    calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
           and n.func.id=='run_monitor']
    assert len(calls)==1
    assert any(k.arg=='sec' and isinstance(k.value,ast.Constant) and k.value.value is True
               for k in calls[0].keywords)


def test_export_mode_is_offline():
    source=(ROOT/'mozes/pipeline_v2c.py').read_text()
    tree=ast.parse(source)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    branch=next(n for n in fn.body if isinstance(n,ast.If) and 'export' in ast.unparse(n.test))
    text=ast.unparse(branch)
    for forbidden in ('run_pipeline(', 'refresh_financials(', 'get_text(', 'refresh_live_prices(', 'identity_audit('):
        assert forbidden not in text


def test_dispatch_checks_current_head_and_restore_is_not_executable():
    source = (ROOT / '.github/workflows/lightweight-monitor.yml').read_text()
    assert 'python -m mozes.publication_control dispatch' in source
    control = (ROOT / 'mozes/publication_control.py').read_text()
    assert "source_run_id={os.environ['GITHUB_RUN_ID']}" in control
    assert "'workflow', 'run', 'pages.yml'" in control
    # Pages workflow owns the HEAD-currency guard; hot producer only hands off the artifact.
    pages = (ROOT / '.github/workflows/pages.yml').read_text()
    assert "git rev-parse HEAD" in pages
    restore = (ROOT / 'scripts/restore_monitor_state.py').read_text()
    assert 'head_branch' in restore and 'main' in restore
    assert 'PRAGMA quick_check' in restore
    assert 'MAX_REWIND' in restore
    assert 'Refusing to rewind' in restore


def test_new_change_cursor_triggers_publication_without_price_or_score_change():
    from mozes.pipeline_v2c import semantic_fingerprint
    p = {'live': [], 'summary': {}, 'build': {'change_cursor': None}}
    before = semantic_fingerprint(p)
    p['build']['change_cursor'] = {'change_id': 'CT-CHANGE-1', 'detected_at': '2026-10-01T12:00:00Z'}
    assert semantic_fingerprint(p) != before
    stable = semantic_fingerprint(p)
    p['generated_at'] = '2026-10-01T13:00:00Z'
    assert semantic_fingerprint(p) == stable


def test_official_feed_status_change_triggers_publication_but_check_time_does_not():
    from mozes.pipeline_v2c import semantic_fingerprint
    p = {'live': [], 'summary': {}, 'official_feeds': [
        {'ticker': 'TEST', 'status': 'no_rss_found', 'checked_at': '2026-10-01T12:00:00Z'}]}
    before = semantic_fingerprint(p)
    p['official_feeds'][0]['checked_at'] = '2026-10-01T18:00:00Z'
    assert semantic_fingerprint(p) == before
    p['official_feeds'][0]['status'] = 'active'
    p['official_feeds'][0]['feed'] = 'https://example.com/rss'
    assert semantic_fingerprint(p) != before


def test_same_day_price_change_is_not_lost_to_daily_date_gate():
    from mozes.pipeline_v2c import semantic_fingerprint
    p = {'live': [{'id': 'AAA-P3', 'market': {'last_close': {'date':'2026-10-01', 'close':10}}}]}
    before = semantic_fingerprint(p)
    p['live'][0]['market']['last_close']['close'] = 11
    assert semantic_fingerprint(p) != before


def test_ledger_insertion_changes_fingerprint_even_if_latest_cursor_is_unchanged(tmp_path, monkeypatch):
    from mozes import db
    from mozes.live_monitor import record_change
    from mozes.pipeline_v2c import export_payload, semantic_fingerprint, unpublished_changes
    import json
    conn = db.connect(tmp_path / 'ledger.db')
    def add(ticker):
        return record_change(conn, ticker=ticker, change_type='news_signal', previous_value=None,
                             new_value={'headline': ticker}, source_url='https://example.com/',
                             source_type='secondary_news', verification_state='investigation_only')
    newest = add('FIRST')
    before = export_payload(conn, tmp_path / 'data.json')
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'change_ledger': before['build']['change_ledger']}))
    assert not unpublished_changes(before, receipt)
    monkeypatch.setattr('mozes.live_monitor._now', lambda: '2000-01-01')
    add('SECOND')
    after = export_payload(conn, tmp_path / 'data.json')
    assert after['build']['change_cursor']['change_id'] == newest
    assert semantic_fingerprint(before) != semantic_fingerprint(after)
    assert unpublished_changes(after, receipt)
    # A publish request and another unchanged scan cannot acknowledge a failed deployment.
    assert unpublished_changes(export_payload(conn, tmp_path / 'data.json'), receipt)
    receipt.write_text(json.dumps({'change_ledger': after['build']['change_ledger']}))
    assert not unpublished_changes(after, receipt)


def test_pages_receipt_is_uploaded_after_deployment():
    source = (ROOT / '.github/workflows/pages.yml').read_text()
    assert source.index('uses: actions/deploy-pages@v4') < source.index('name: mozes-published-ledger')
    assert 'restore_published_receipt(repo)' in (ROOT / 'scripts/restore_monitor_state.py').read_text()
