"""Build Stage A Worker candidate and inject the PR #78 idle-monitor watchdog only."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from scripts.build_stage_a_stabilized import build as build_stage_a

WATCHDOG_METHOD = r'''
  async idleMonitorWatchdog(health) {
    if (this.env.EDGE_MONITOR_WATCHDOG_ENABLED !== '1') return;
    // Publication age is only a liveness hint, never checkpoint/recovery proof.
    let feed;
    try {
      feed = await this.env.ALERTS_DB.prepare('SELECT generated_at FROM alert_feed WHERE id=1').first();
    } catch (error) {
      this.failure(health, 'reconciliation', new Date().toISOString(), error);
      return;
    }
    const stamp = Date.parse(feed?.generated_at), age = Date.now() - stamp;
    health.monitor_liveness = {
      feed_generated_at: feed?.generated_at || null,
      status: !Number.isFinite(age) || age < 0 ? 'UNKNOWN'
        : age > 2400000 ? 'RECOVERY_REQUIRED'
        : age >= 1200000 ? 'RECONCILIATION_DUE'
        : 'RECENT_HINT',
    };
    // RECOVERY_REQUIRED still requests the serialized producer: Actions independently
    // proves newest-checkpoint continuity (evidence audit) and never rewinds lineage.
    const due = health.monitor_liveness.status === 'RECONCILIATION_DUE'
      || health.monitor_liveness.status === 'RECOVERY_REQUIRED';
    if (!due || !this.env.GITHUB_TOKEN) return;
    const last = await this.state.storage.get('reconciliation_requested_at') || 0;
    if (Date.now() - last < 900000) {
      health.monitor_liveness.status = 'THROTTLED_UNVERIFIED';
      return;
    }
    try {
      // Persist before I/O: ambiguous remote acceptance cannot cause an immediate retry.
      await this.state.storage.put('reconciliation_requested_at', Date.now());
      const response = await fetch(
        `https://api.github.com/repos/${this.env.GITHUB_REPO}/actions/workflows/${this.env.WORKFLOW_FILE || 'lightweight-monitor.yml'}/dispatches`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${this.env.GITHUB_TOKEN}`,
            Accept: 'application/vnd.github+json',
            'User-Agent': 'mozes-hot-edge',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ ref: this.env.GITHUB_REF || 'main', inputs: { priority_only: 'true' } }),
          signal: AbortSignal.timeout(8000),
        },
      );
      if (response.status !== 204) throw Error('reconciliation dispatch failed');
      await this.state.storage.put('reconciliation_requested_at', Date.now());
      health.monitor_liveness.status = 'REQUESTED_UNVERIFIED';
      this.success(health, 'reconciliation', new Date().toISOString());
    } catch (error) {
      this.failure(health, 'reconciliation', new Date().toISOString(), error);
    }
  }
'''

ALARM_HOOK = (
    "      try { await this.flushPending(); this.success(health, \"downstream\", now); }\n"
    "      catch (error) { this.failure(health, \"downstream\", now, error); }\n"
    "      try { await this.idleMonitorWatchdog(health); }\n"
    "      catch (error) { this.failure(health, \"reconciliation\", new Date().toISOString(), error); }"
)


def inject(hot_edge: Path) -> None:
    text = hot_edge.read_text(encoding='utf-8')
    if 'EDGE_MONITOR_WATCHDOG_ENABLED' in text:
        raise SystemExit('watchdog already present in Stage A candidate')
    flush = (
        '      try { await this.flushPending(); this.success(health, "downstream", now); }\n'
        '      catch (error) { this.failure(health, "downstream", now, error); }'
    )
    if flush not in text:
        raise SystemExit('Stage A flushPending alarm hook not found')
    text = text.replace(flush, ALARM_HOOK, 1)
    # Insert method before the first success() helper.
    marker = '\n  success(health, source, now) {'
    if marker not in text:
        raise SystemExit('Stage A success() helper not found')
    text = text.replace(marker, '\n' + WATCHDOG_METHOD + marker, 1)
    if text.count('EDGE_MONITOR_WATCHDOG_ENABLED') != 1:
        raise SystemExit('watchdog injection failed uniqueness check')
    if 'source_item_id' in text:
        raise SystemExit('Stage B source_item_id leaked into Stage A candidate')
    hot_edge.write_text(text, encoding='utf-8')


def harden_wrangler(path: Path) -> None:
    text = path.read_text(encoding='utf-8')
    if 'keep_vars' not in text:
        text = text.replace('compatibility_date = "2026-08-01"\n',
                            'compatibility_date = "2026-08-01"\nkeep_vars = true\n', 1)
    # Never let the Stage A template overwrite production's 120s interval.
    text = re.sub(r'EDGE_INTERVAL_SECONDS\s*=\s*"\d+"', 'EDGE_INTERVAL_SECONDS = "120"', text, count=1)
    path.write_text(text, encoding='utf-8')


def build(destination: Path) -> Path:
    root = build_stage_a(destination)
    cloudflare = root / 'cloudflare'
    inject(cloudflare / 'src' / 'hot-edge.js')
    harden_wrangler(cloudflare / 'wrangler.toml')
    migrations = sorted(p.name for p in (cloudflare / 'migrations').glob('*.sql'))
    if migrations != ['0001_alert_feed.sql', '0002_hot_edge.sql',
                      '0003_hot_edge_reliability.sql', '0004_clinical_candidates.sql']:
        raise SystemExit(f'unexpected migrations: {migrations}')
    if (cloudflare / 'migrations' / '0005_d1_optimization.sql').exists():
        raise SystemExit('migration 0005 must not be present')
    return root


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(build(args.destination).resolve())
