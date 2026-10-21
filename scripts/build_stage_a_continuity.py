"""Add only opt-in producer liveness to the immutable stabilized Stage A build."""
import argparse
from pathlib import Path
import shutil

from build_stage_a_stabilized import build as stabilized

ROOT = Path(__file__).resolve().parents[1]


def replace(path, old, new):
    raw = path.read_bytes()
    # The pinned build contains CRLF in its earlier stabilization patch.
    content = raw.decode('utf-8').replace('\r\n', '\n')
    if content.count(old) != 1:
        raise ValueError(f'Stage A anchor mismatch: {path.name}')
    path.write_text(content.replace(old, new), encoding='utf-8', newline='\n')


def build(destination):
    candidate = stabilized(destination)
    directory = candidate / 'cloudflare/src'
    shutil.copy2(ROOT / 'cloudflare/src/monitor-heartbeat.js', directory)
    edge = directory / 'hot-edge.js'
    replace(edge, 'const SEC = ', "import {MonitorHeartbeat} from './monitor-heartbeat.js';\nconst SEC = ")
    replace(edge, 'constructor(state, env) { this.state = state; this.env = env; }',
            'constructor(state, env) { this.state = state; this.env = env; '
            'this.monitorHeartbeat = new MonitorHeartbeat(state.storage, env); }')
    replace(edge, '      return Response.json({ ...health, next_alarm_at:',
            "      health.monitor_heartbeat = this.env.EDGE_MONITOR_HEARTBEAT_ENABLED === '1'\n"
            "        ? await this.state.storage.get('monitor_heartbeat') || {status:'UNKNOWN'} : {status:'DISABLED'};\n"
            '      return Response.json({ ...health, next_alarm_at:')
    replace(edge, '  async runAlarm() {', '  async runAlarm() {\n    let heartbeatCycle=false;')
    replace(edge, '    try {\n      const sources = ',
            "    try {\n      // GitHub-only budget; source polling resumes on the next existing alarm.\n"
            "      if (this.env.EDGE_MONITOR_HEARTBEAT_ENABLED === '1' && await this.monitorHeartbeat.due(started)) {\n"
            "        await this.monitorHeartbeat.check(started);\n"
            "        if (this.env.GITHUB_TOKEN) {heartbeatCycle=true;return;}\n      }\n      const sources = ")
    replace(edge, '    } finally {\n      await this.state.storage.setAlarm(Date.now() + INTERVAL(this.env));',
            '    } finally {\n      await this.state.storage.setAlarm(Date.now() + (heartbeatCycle ? 1000 : INTERVAL(this.env)));')
    index = directory / 'index.js'
    replace(index, '      const failed = sources.filter',
            "      const producerDegraded = health.monitor_heartbeat && !['FRESH','DISABLED'].includes(health.monitor_heartbeat.status);\n"
            '      const failed = sources.filter')
    replace(index, 'failed || backlog.analysis_blocked', 'failed || producerDegraded || backlog.analysis_blocked')
    # No Stage B source file/configuration/migration is copied into the candidate.
    return candidate


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    print(build(parser.parse_args().destination).resolve())
