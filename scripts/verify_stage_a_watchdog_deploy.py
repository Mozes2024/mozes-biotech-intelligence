"""Read-only post-deploy checks for the Stage A idle-watchdog rollout."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from email.parser import BytesParser
import email.policy
from pathlib import Path

ACCOUNT = '75b91ca20508a72979cb5337ce1cc7e2'
DATABASE = '9baf99f2-1ccc-4415-affd-2708a7210e7b'
WORKER = 'mozes-hot-clock'
FORWARD = '2026-10-20T18:24:29.424Z'
OUT = Path('.diagnostics/stage-a-watchdog')


def require(condition, reason):
    if not condition:
        raise SystemExit(reason)


def cf(path, *, token_name='CLOUDFLARE_API_TOKEN', sql=None, raw=False):
    require(sql is None or sql.lstrip().upper().startswith(('SELECT ', 'PRAGMA ')), 'non_read_query_refused')
    data = json.dumps({'sql': sql}).encode() if sql else None
    request = urllib.request.Request(
        'https://api.cloudflare.com/client/v4/' + path,
        data=data,
        headers={'Authorization': 'Bearer ' + os.environ[token_name], 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            if raw:
                return body, response.headers.get('Content-Type', '')
            payload = json.loads(body)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'{token_name}_HTTP_{exc.code}_on_{path.split("?")[0]}') from None
    require(payload.get('success') is True, f'cloudflare_api_unsuccessful:{path}')
    return payload['result']


def bundle_bytes():
    raw, kind = cf(f'accounts/{ACCOUNT}/workers/scripts/{WORKER}/content/v2', raw=True)
    if 'multipart/' in kind:
        message = BytesParser(policy=email.policy.default).parsebytes(
            ('Content-Type: ' + kind + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + raw)
        parts = [p.get_payload(decode=True) for p in message.walk() if p.get_filename() == 'index.js']
        require(len(parts) == 1, 'deployed_bundle_identity_unavailable')
        return parts[0]
    return raw


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    settings = cf(f'accounts/{ACCOUNT}/workers/scripts/{WORKER}/settings')
    deployments = cf(f'accounts/{ACCOUNT}/workers/scripts/{WORKER}/deployments')['deployments']
    latest = max(deployments, key=lambda row: row['created_on'])
    require(len(latest['versions']) == 1 and latest['versions'][0]['percentage'] == 100, 'split_Worker_deployment')
    version = latest['versions'][0]['version_id']
    bindings = settings['bindings']
    values = {b['name']: b.get('text') for b in bindings if b['type'] == 'plain_text'}
    secrets = sorted(b['name'] for b in bindings if b['type'] == 'secret_text')
    require(values.get('EDGE_MONITOR_WATCHDOG_ENABLED') == '1', 'watchdog_flag_missing')
    require(values.get('EDGE_INTERVAL_SECONDS') == '120', 'interval_changed')
    require(values.get('EDGE_FORWARD_ONLY_FROM') == FORWARD, 'forward_boundary_changed')
    require(any(b.get('name') == 'ALERTS_DB' and b.get('id') == DATABASE for b in bindings), 'D1_binding_changed')
    require(any(b.get('name') == 'HOT_EDGE' and b.get('type') == 'durable_object_namespace' for b in bindings),
            'DO_binding_missing')
    schedule = cf(f'accounts/{ACCOUNT}/workers/scripts/{WORKER}/schedules')['schedules']
    require([row['cron'] for row in schedule] == ['*/2 * * * *'], 'cron_changed')
    bundle = bundle_bytes()
    require(b'source_item_id' not in bundle, 'Stage_B_bundle_detected')
    require(b'EDGE_MONITOR_WATCHDOG_ENABLED' in bundle, 'watchdog_code_missing')
    require(b'idleMonitorWatchdog' in bundle, 'watchdog_method_missing')
    migrations = cf(
        f'accounts/{ACCOUNT}/d1/database/{DATABASE}/query',
        token_name='CLOUDFLARE_RECOVERY_READ_TOKEN',
        sql='SELECT name FROM d1_migrations ORDER BY id',
    )
    require(isinstance(migrations, list) and len(migrations) == 1 and migrations[0].get('success') is True,
            'D1_read_unconfirmed')
    names = [row['name'] for row in migrations[0]['results']]
    require([name[:4] for name in names] == ['0001', '0002', '0003', '0004'], 'D1_migrations_changed')
    proof = {
        'worker_version': version,
        'bundle_sha256': hashlib.sha256(bundle).hexdigest(),
        'watchdog_enabled': True,
        'interval_seconds': 120,
        'forward_only_from': FORWARD,
        'cron': ['*/2 * * * *'],
        'secret_names': secrets,
        'd1_migrations': names,
        'plain_text_watchdog': values.get('EDGE_MONITOR_WATCHDOG_ENABLED'),
    }
    (OUT / 'proof-postdeploy.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
