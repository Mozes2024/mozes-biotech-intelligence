"""Approved November 2 identity-bound exception. No timestamp correction or fallback."""
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

REPO = 'Mozes2024/mozes-biotech-intelligence'
SOURCE = '41288520060'
ANCESTOR = '41286857380'
HEAD = '761b9790f207399cace10b78b207d3ff2ad6bfcd'
DIGEST = '1ea5fc0de96de4ba316a32a26e0fed311044a04a1fc172888cadc13ded129feb'
ANCESTOR_DIGEST = '34ea8b823fe185be4dfa15a9ce29539b6645df526c9b785410d1d71fd52b9d86'
ARTIFACT = 13075184974
STAMP = '2026-11-01T08:23:53+00:00'


def require(ok, reason):
    if not ok:
        raise RuntimeError('November lineage: ' + reason)


def eligible(repo, candidate, source):
    return (repo == REPO and str(candidate) == SOURCE
            and hashlib.sha256(source.read_bytes()).hexdigest() == DIGEST)


def verify(repo, candidate, source, checkpoint, manifest, gh, download, latest, *, now):
    from .github_inventory import inventory
    from .monitor_recovery import audit_producers, verify_durable_history
    require(eligible(repo, candidate, source), 'identity mismatch')
    require(checkpoint == {'schema': 1, 'producer_run_id': SOURCE,
                           'created_at': STAMP, 'db_sha256': DIGEST}, 'checkpoint mismatch')
    require(manifest.get('source_sha') == HEAD and manifest.get('pipeline_complete') is True
            and manifest.get('data_publishable') is True
            and manifest.get('generated_at') == '2026-11-01T08:23:52+00:00', 'manifest mismatch')

    def newest():
        rows = latest()
        require(rows and str(rows[0]['databaseId']) == SOURCE, 'superseded source')
        artifacts = json.loads(gh('api', f'repos/{repo}/actions/runs/{SOURCE}/artifacts?per_page=100'))
        require(artifacts.get('total_count') == len(artifacts.get('artifacts', [])), 'incomplete artifacts')
        found = [a for a in artifacts['artifacts'] if a.get('name') == 'mozes-live-monitor']
        require(len(found) == 1 and found[0].get('id') == ARTIFACT
                and found[0].get('expired') is False
                and found[0].get('workflow_run', {}).get('head_sha') == HEAD, 'artifact mismatch')
    newest()
    for run_id in (SOURCE, ANCESTOR):
        meta = json.loads(gh('api', f'repos/{repo}/actions/runs/{run_id}'))
        require(str(meta.get('id')) == run_id and meta.get('head_branch') == 'main'
                and meta.get('head_repository', {}).get('full_name') == repo
                and meta.get('name') == 'lightweight-live-monitor'
                and meta.get('head_sha') == HEAD and meta.get('status') == 'completed'
                and meta.get('conclusion') == 'success' and meta.get('run_attempt') == 1,
                'producer provenance mismatch')
    with tempfile.TemporaryDirectory() as tmp:
        download(ANCESTOR, tmp)
        parent = Path(tmp) / 'mozes-live.db'
        require(parent.is_file() and hashlib.sha256(parent.read_bytes()).hexdigest() == ANCESTOR_DIGEST,
                'ancestor hash mismatch')
        parent_cp = json.loads((Path(tmp) / 'edge-checkpoint.json').read_text())
        require(parent_cp == {'schema': 1, 'producer_run_id': ANCESTOR,
                              'created_at': '2026-11-01T07:59:46+00:00',
                              'db_sha256': ANCESTOR_DIGEST}, 'ancestor checkpoint mismatch')
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as conn:
            conn.execute('ATTACH DATABASE ? AS ancestor', (parent.as_uri() + '?mode=ro',))
            require(conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'integrity failure')
            require(conn.execute('PRAGMA ancestor.quick_check').fetchone()[0] == 'ok', 'ancestor integrity failure')
            for table in ('change_events', 'alert_outbox', 'edge_event_links'):
                require(not conn.execute(f'SELECT * FROM ancestor.{table} EXCEPT SELECT * FROM main.{table} LIMIT 1').fetchall(),
                        'ancestor state not incorporated')

    def api(path):
        return json.loads(gh('api', f'repos/{repo}/actions/{path}', timeout=30))
    def collection(value, key, *, partial=False):
        require(type(value.get('total_count')) is int and isinstance(value.get(key), list), 'incomplete inventory')
        require(value['total_count'] >= len(value[key]), 'invalid inventory count')
        return value[key]
    all_runs = inventory('workflows/lightweight-monitor.yml/runs?exclude_pull_requests=true',
                         'workflow_runs', api=api, collection=collection, bound=60)
    # Only the exact hash-bound ancestor can be excluded. Do not infer ordering
    # from run numbers or subtract an hour from either API timestamps.
    rows = []
    since = datetime.fromisoformat(STAMP)
    for run in all_runs:
        require(isinstance(run.get('updated_at'), str), 'missing update time')
        updated = datetime.fromisoformat(run['updated_at'].replace('Z', '+00:00'))
        require(updated.tzinfo is not None, 'unqualified update time')
        if str(run['id']) not in (SOURCE, ANCESTOR) and (run.get('status') != 'completed' or updated > since):
            rows.append(run)
    require(len(rows) <= 500, 'too many relevant producers')
    def bounded_gh(*args, **kwargs):
        if args[0] == 'api' and '/actions/workflows/lightweight-monitor.yml/runs?' in args[1]:
            page = int(parse_qs(urlsplit(args[1]).query)['page'][0])
            return json.dumps({'workflow_runs': rows[(page-1)*100:page*100]})
        return gh(*args, **kwargs)
    proof = audit_producers(repo, since, bounded_gh, now=now, full_history=True, source_run_id=SOURCE)
    proof.update(verify_durable_history(source))
    newest()  # Read again after every remote check, before restore may write.
    proof.update(source_run_id=SOURCE, source_sha256=DIGEST, incorporated_ancestor=ANCESTOR,
                 inventory_count=len(all_runs), exception='November-2-exact-lineage')
    return proof
