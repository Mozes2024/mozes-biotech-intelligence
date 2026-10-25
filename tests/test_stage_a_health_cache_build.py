"""The Stage A health-cache build caches only polling routes and keeps production intact."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'stage_a_health_cache_builder', ROOT / 'scripts' / 'build_stage_a_health_cache.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
build = builder.build


def test_health_cache_guard_injected(tmp_path):
    root = build(tmp_path / 'candidate')
    index = (root / 'cloudflare' / 'src' / 'index.js').read_text(encoding='utf-8')
    # fetch receives the execution context for waitUntil cache writes
    assert 'async fetch(request, env, ctx) {' in index
    assert 'caches.default.match(request)' in index
    assert 'caches.default.put(request' in index
    assert 'max-age=300' in index
    # cache consulted before any route touches D1 or the Durable Object
    assert index.index('HEALTH_CACHE_PATHS.has(path)') < index.index('"/edge/metrics"')
    # only the two polling routes are cached
    assert 'HEALTH_CACHE_PATHS = new Set(["/edge/health", "/edge/metrics"])' in index
    # /alerts alert delivery stays uncached
    assert 'if (path === "/alerts") return handleAlertFeed(request, env);' in index
    # non-GET requests and non-200 responses are never cached
    assert 'response.status !== 200' in index
    assert 'request.method !== "GET"' in index


def test_production_semantics_preserved(tmp_path):
    root = build(tmp_path / 'candidate')
    index = (root / 'cloudflare' / 'src' / 'index.js').read_text(encoding='utf-8')
    # watchdog build stays intact
    hot = (root / 'cloudflare' / 'src' / 'hot-edge.js').read_text(encoding='utf-8')
    assert 'EDGE_MONITOR_WATCHDOG_ENABLED' in hot
    assert 'idleMonitorWatchdog' in hot
    # forward-only fence stays in the bundle
    assert "import {queueHealth} from './forward-policy.js';" in index
    # same routes as production
    for route in ['"/alerts"', '"/edge/sync"', '"/edge/ack"', '"/edge/candidates"',
                  '"/edge/candidate/ack"', '"/edge/investigate"', '"/edge/event"',
                  '"/edge/metrics"', '"/edge/health"']:
        assert route in index
    # no new migrations
    migrations = sorted(p.name for p in (root / 'cloudflare' / 'migrations').glob('*.sql'))
    assert migrations == [
        '0001_alert_feed.sql', '0002_hot_edge.sql',
        '0003_hot_edge_reliability.sql', '0004_clinical_candidates.sql',
    ]
    toml = (root / 'cloudflare' / 'wrangler.toml').read_text(encoding='utf-8')
    assert 'keep_vars = true' in toml
