"""Bounded runner-only controls for the explicitly approved October 21 operation."""
import argparse
import email.policy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.parser import BytesParser
from pathlib import Path

REPO = 'Mozes2024/mozes-biotech-intelligence'
SOURCE = '39474053120'
DB_HASH = '94bda318e9f8e553709b295d5249cebf269002cefc4d718c0cd51f1dc21192c1'
OLD_VERSION = '4d60c332-f2fb-40fe-8994-c0d5b0ac7cf6'
FORWARD = '2026-10-20T18:24:29.424Z'
ACCOUNT = '75b91ca20508a72979cb5337ce1cc7e2'
DATABASE = '9baf99f2-1ccc-4415-affd-2708a7210e7b'
WORKER = 'mozes-hot-clock'
AVXL = 'EDGE-56b2d11f7fa9702747a6857a'
OUT = Path('.diagnostics/bio-continuity')
proof = {'observed_at': datetime.now(timezone.utc).isoformat(), 'status': 'HOLD', 'read_only': True}

def require(value, reason):
    if not value:
        raise RuntimeError(reason)

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def gh(*args):
    result = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=300)
    if result.returncode:
        code = '401' if '401' in result.stderr else '403' if '403' in result.stderr else 'unavailable'
        raise RuntimeError('GitHub_GET_' + code)
    return result.stdout

def cf(path, *, sql=None, raw=False):
    # The only POST here is D1's fixed SELECT-only query endpoint.
    require(sql is None or sql.lstrip().upper().startswith(('SELECT ', 'PRAGMA TABLE_INFO')), 'non_read_query_refused')
    request = urllib.request.Request('https://api.cloudflare.com/client/v4/' + path,
        data=json.dumps({'sql': sql}).encode() if sql else None,
        headers={'Authorization': 'Bearer ' + os.environ['CLOUDFLARE_API_TOKEN'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body, content_type = response.read(8_000_001), response.headers.get('Content-Type', '')
    except urllib.error.HTTPError as exc:
        scope = 'D1_SELECT_query' if '/d1/' in path else 'Workers_Scripts_read'
        raise RuntimeError(f'Cloudflare_{scope}_HTTP_{exc.code}') from None
    require(len(body) <= 8_000_000, 'Cloudflare_response_bound')
    if raw:
        return body, content_type
    value = json.loads(body)
    require(value.get('success') is True, 'Cloudflare_operation_not_confirmed')
    return value['result']

def query(sql):
    value = cf(f'accounts/{ACCOUNT}/d1/database/{DATABASE}/query', sql=sql)
    require(len(value) == 1 and value[0].get('success') is True, 'D1_read_unconfirmed')
    return value[0]['results']

def snapshot(*, deployed=False, candidate=None):
    require(os.environ['CLOUDFLARE_ACCOUNT_ID'] == ACCOUNT, 'Cloudflare_account_identity_changed')
    base = f'accounts/{ACCOUNT}/workers/scripts/{WORKER}'
    settings = cf(base + '/settings')
    deployments = cf(base + '/deployments')['deployments']
    latest = max(deployments, key=lambda x: x['created_on'])
    require(len(latest['versions']) == 1 and latest['versions'][0]['percentage'] == 100, 'split_Worker_deployment')
    version = latest['versions'][0]['version_id']
    require(version != OLD_VERSION if deployed else version == OLD_VERSION, 'reviewed_Worker_version_changed')
    bindings = settings['bindings']
    values = {b['name']: b.get('text') for b in bindings if b['type'] == 'plain_text'}
    require(values.get('EDGE_FORWARD_ONLY_FROM') == FORWARD, 'forward_boundary_changed')
    require(values.get('EDGE_INTERVAL_SECONDS') == '120', 'source_interval_changed')
    require(values.get('EDGE_MONITOR_HEARTBEAT_ENABLED') == '1' if deployed else values.get('EDGE_MONITOR_HEARTBEAT_ENABLED') != '1', 'heartbeat_activation_changed')
    require(any(b['name'] == 'ALERTS_DB' and b.get('id') == DATABASE for b in bindings), 'D1_binding_changed')
    require(any(b['name'] == 'HOT_EDGE' and b['type'] == 'durable_object_namespace' for b in bindings), 'DO_binding_missing')
    if candidate:
        config = tomllib.loads(candidate.read_text())
        require(config['name'] == WORKER and config['main'] == 'src/index.js' and config.get('keep_vars') is True, 'candidate_identity_changed')
        require(all(values.get(key) == str(value) for key, value in config['vars'].items()
                    if key != 'EDGE_MONITOR_HEARTBEAT_ENABLED'), 'candidate_would_change_existing_variables')
        require(config['triggers']['crons'] == ['*/2 * * * *'], 'candidate_cron_changed')
        require([(b['binding'], b['database_id']) for b in config['d1_databases']] == [('ALERTS_DB', DATABASE)], 'candidate_D1_changed')
        require(config['durable_objects']['bindings'] == [{'name': 'HOT_EDGE', 'class_name': 'HotEdge'}], 'candidate_DO_changed')
        require(config['migrations'] == [{'tag': 'hot-edge-v3', 'new_sqlite_classes': ['HotEdge']}], 'candidate_DO_migration_changed')
        require(sorted(p.name[:4] for p in (candidate.parent / 'migrations').glob('*.sql')) == ['0001','0002','0003','0004'], 'candidate_D1_migrations_changed')
    schedule = cf(base + '/schedules')['schedules']
    require([x['cron'] for x in schedule] == ['*/2 * * * *'], 'cron_changed')
    migrations = query('SELECT name FROM d1_migrations ORDER BY id')
    require([x['name'][:4] for x in migrations] == ['0001', '0002', '0003', '0004'], 'D1_migrations_changed')
    columns = query('PRAGMA table_info(edge_events)')
    require('source_item_id' not in [x['name'] for x in columns], 'Stage_B_schema_detected')
    events = query('SELECT * FROM edge_events ORDER BY first_seen_at,event_id LIMIT 1001')
    require(len(events) < 1001, 'event_inventory_bound')
    candidates = query('SELECT * FROM edge_candidates ORDER BY first_seen_at,candidate_id LIMIT 1001')
    require(len(candidates) < 1001, 'candidate_inventory_bound')
    raw, kind = cf(base + '/content/v2', raw=True)
    if 'multipart/' in kind:
        message = BytesParser(policy=email.policy.default).parsebytes(('Content-Type: ' + kind + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + raw)
        parts = [p.get_payload(decode=True) for p in message.walk() if p.get_filename() == 'index.js']
        require(len(parts) == 1, 'deployed_bundle_identity_unavailable')
        bundle = parts[0]
    else:
        bundle = raw
    require(b'source_item_id' not in bundle, 'Stage_B_bundle_detected')
    require((b'monitor_heartbeat' in bundle) == deployed, 'heartbeat_bundle_mismatch')
    normalized = sorted((b for b in bindings if b['name'] != 'EDGE_MONITOR_HEARTBEAT_ENABLED'), key=lambda b: b['name'])
    result = {'worker_version': version, 'bundle_sha256': hashlib.sha256(bundle).hexdigest(),
              'preserved_bindings_hash': digest(normalized), 'secret_names': sorted(b['name'] for b in bindings if b['type'] == 'secret_text'),
              'forward_only_from': FORWARD, 'interval_seconds': 120, 'cron': ['*/2 * * * *'],
              'd1_migrations': [x['name'] for x in migrations], 'events_hash': digest(events),
              'candidates_hash': digest(candidates), 'event_count': len(events),
              'rapp': [x for x in events if x['ticker'] == 'RAPP'],
              'avxl': [x for x in events if x['event_id'] == AVXL]}
    require(len(result['avxl']) == 1, 'AVXL_identity_missing')
    require(result['rapp'], 'RAPP_history_missing')
    return result, events

def verify():
    state, events = snapshot()
    proof['cloudflare'] = state
    # Use the existing verifier exactly; it repeats newest/history/Edge/feed gates.
    result = subprocess.run([sys.executable, 'scripts/restore_monitor_state.py', '--run-id', SOURCE,
                             '--verify-recovery-only'], capture_output=True, text=True, timeout=600)
    require(result.returncode == 0, 'native_recovery_verifier_failed: ' + result.stderr[-300:])
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    require(rows and rows[-1].get('status') == 'VERIFIED_NO_WRITES', 'native_verification_proof_missing')
    proof['native_verifier'] = rows[-1]
    source = OUT / 'source'
    gh('run', 'download', SOURCE, '--repo', REPO, '--name', 'mozes-live-monitor', '--dir', str(source))
    path = source / 'mozes-live.db'
    require(hashlib.sha256(path.read_bytes()).hexdigest() == DB_HASH, 'approved_database_hash_changed')
    by_id = {r['event_id']: r for r in events}
    from mozes.edge_enrichment import ack_payload
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        require(conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'SQLite_integrity')
        require(not conn.execute('PRAGMA foreign_key_check').fetchall(), 'SQLite_foreign_keys')
        links = conn.execute('SELECT * FROM edge_event_links').fetchall()
        for link in links:
            current = by_id.get(link['edge_event_id'])
            require(current and current['enrichment_ack_at'] and current['enrichment_change_id'] == link['change_id'], 'retained_ACK_changed')
            expected = ack_payload(conn, link['edge_event_id'])
            deliveries = json.loads(current['enrichment_delivery_json'] or '[]')
            require(sorted(deliveries, key=lambda x: x['alert_id']) == sorted(expected['delivery'], key=lambda x: x['alert_id']), 'delivery_receipt_changed')
        proof['retained_links_verified'] = len(links)
        proof['internal_database_sha256'] = DB_HASH
        proof['additional_events_since_checkpoint'] = [{k: r.get(k) for k in ('event_id','ticker','first_seen_at','analysis_status','lifecycle','enrichment_ack_at','enrichment_github_run_id')} for r in events if r['first_seen_at'] > '2026-10-20T18:24:11Z']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['verify', 'inspect', 'postdeploy'])
    parser.add_argument('--candidate', type=Path)
    args = parser.parse_args()
    require(os.environ.get('GITHUB_REPOSITORY') == REPO and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'trusted_main_first_attempt_required')
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        if args.mode == 'verify':
            verify()
        else:
            proof['cloudflare'], _ = snapshot(deployed=args.mode == 'postdeploy', candidate=args.candidate)
        proof['status'] = 'PASS'
    except Exception as exc:
        proof['blocker'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(proof['blocker']) from None
    finally:
        (OUT / ('proof-' + args.mode + '.json')).write_text(json.dumps(proof, indent=2))
        print(json.dumps({'mode': args.mode, 'status': proof['status'], 'blocker': proof.get('blocker')}))

if __name__ == '__main__':
    main()
