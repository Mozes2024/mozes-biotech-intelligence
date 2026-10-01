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


def test_single_producer_and_explicit_deep_slot():
    source=(ROOT/'.github/workflows/lightweight-monitor.yml').read_text()
    assert 'group: mozes-live-data-producer' in source
    assert "github.event.schedule == '20 6 * * 1,3,5'" in source
    assert "steps.data.outputs.should_publish == 'true'" in source
    assert 'timeout-minutes:' in source


def test_export_mode_is_offline():
    source=(ROOT/'mozes/pipeline_v2c.py').read_text()
    tree=ast.parse(source)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    branch=next(n for n in fn.body if isinstance(n,ast.If) and 'export' in ast.unparse(n.test))
    text=ast.unparse(branch)
    for forbidden in ('run_pipeline(', 'refresh_financials(', 'get_text(', 'refresh_live_prices(', 'identity_audit('):
        assert forbidden not in text


def test_dispatch_checks_current_head_and_restore_is_not_executable():
    source=(ROOT/'.github/workflows/lightweight-monitor.yml').read_text()
    assert '[ "$current" = "$GITHUB_SHA" ]' in source
    restore=(ROOT/'scripts/restore_monitor_state.py').read_text()
    assert "meta.get('head_branch') != 'main'" in restore
    assert 'PRAGMA quick_check' in restore


def test_new_change_cursor_triggers_publication_without_price_or_score_change():
    from mozes.pipeline_v2c import semantic_fingerprint
    p = {'live': [], 'summary': {}, 'build': {'change_cursor': None}}
    before = semantic_fingerprint(p)
    p['build']['change_cursor'] = {'change_id': 'CT-CHANGE-1', 'detected_at': '2026-10-01T12:00:00Z'}
    assert semantic_fingerprint(p) != before
    stable = semantic_fingerprint(p)
    p['generated_at'] = '2026-10-01T13:00:00Z'
    assert semantic_fingerprint(p) == stable


def test_same_day_price_change_is_not_lost_to_daily_date_gate():
    from mozes.pipeline_v2c import semantic_fingerprint
    p = {'live': [{'id': 'AAA-P3', 'market': {'last_close': {'date':'2026-10-01', 'close':10}}}]}
    before = semantic_fingerprint(p)
    p['live'][0]['market']['last_close']['close'] = 11
    assert semantic_fingerprint(p) != before
