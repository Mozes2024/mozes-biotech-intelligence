from pathlib import Path
import ast

ROOT=Path(__file__).resolve().parents[1]


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
    assert 'source_run_id="$GITHUB_RUN_ID"' in source
    assert 'gh workflow run pages.yml' in source
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
