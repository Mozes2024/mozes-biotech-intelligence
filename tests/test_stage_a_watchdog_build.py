"""Stage A watchdog builder must stay migration-free and inject one idle path."""
from pathlib import Path

from scripts.build_stage_a_watchdog import build


def test_stage_a_watchdog_injects_idle_path_without_stage_b(tmp_path):
    root = build(tmp_path / 'candidate')
    hot = (root / 'cloudflare' / 'src' / 'hot-edge.js').read_text(encoding='utf-8')
    assert 'EDGE_MONITOR_WATCHDOG_ENABLED' in hot
    assert 'idleMonitorWatchdog' in hot
    assert 'source_item_id' not in hot
    assert 'RECOVERY_REQUIRED' in hot
    assert '900000' in hot  # 15-minute throttle
    migrations = sorted(p.name for p in (root / 'cloudflare' / 'migrations').glob('*.sql'))
    assert migrations == [
        '0001_alert_feed.sql', '0002_hot_edge.sql',
        '0003_hot_edge_reliability.sql', '0004_clinical_candidates.sql',
    ]
    toml = (root / 'cloudflare' / 'wrangler.toml').read_text(encoding='utf-8')
    assert 'keep_vars = true' in toml
    assert 'EDGE_INTERVAL_SECONDS = "120"' in toml
