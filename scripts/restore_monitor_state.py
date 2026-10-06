"""Restore only a main-branch first-party monitor DB. Never execute artifact files."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True, timeout=45)


def restore_published_receipt(repo):
    """A producer's request is not proof of a deployment; trust successful Pages only."""
    destination = Path('.monitor/published-ledger.json')
    destination.unlink(missing_ok=True)
    try:
        runs = json.loads(gh('run', 'list', '--workflow', 'pages.yml', '--branch', 'main',
                             '--status', 'success', '--limit', '5', '--json', 'databaseId'))
    except (subprocess.CalledProcessError, OSError, ValueError):
        return  # Missing acknowledgement keeps publication pending; DB restore still proceeds.
    for run in runs:
        run_id = str(run['databaseId'])
        try:
            meta = json.loads(gh('api', f'repos/{repo}/actions/runs/{run_id}'))
        except (subprocess.CalledProcessError, OSError, ValueError):
            continue
        if (meta.get('head_branch') != 'main' or meta.get('name') != 'deploy-pages'
                or (meta.get('head_repository') or {}).get('full_name') != repo
                or meta.get('conclusion') != 'success'):
            continue
        try:
            with tempfile.TemporaryDirectory() as tmp:
                gh('run', 'download', run_id, '-n', 'mozes-published-ledger', '-D', tmp)
                value = json.loads((Path(tmp) / 'publish-receipt.json').read_text(encoding='utf-8'))
                ledger = value.get('change_ledger') if isinstance(value, dict) else None
                if not isinstance(ledger, str) or len(ledger) != 64:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps({'change_ledger': ledger}), encoding='utf-8')
                return
        except (subprocess.CalledProcessError, OSError, ValueError):
            continue


def restore(run_id=None):
    destination = Path(os.environ.get('MOZES_DB_PATH', '.monitor/mozes-live.db'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    candidates = [str(run_id)] if run_id else [str(x['databaseId']) for x in json.loads(gh(
        'run', 'list', '--workflow', 'lightweight-monitor.yml', '--branch', 'main', '--status', 'success',
        '--limit', '8', '--json', 'databaseId'))]
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    restore_published_receipt(repo)
    for candidate in candidates:
        if not candidate.isdigit():
            raise ValueError('source run ID must be numeric')
        meta = json.loads(gh('api', f'repos/{repo}/actions/runs/{candidate}'))
        if (meta.get('head_branch') != 'main' or meta.get('name') != 'lightweight-live-monitor'
                or (meta.get('head_repository') or {}).get('full_name') != repo
                or meta.get('conclusion') not in {None, 'success'}):
            if run_id:
                raise ValueError('untrusted or unsuccessful source run')
            continue
        try:
            with tempfile.TemporaryDirectory() as tmp:
                gh('run', 'download', candidate, '-n', 'mozes-live-monitor', '-D', tmp)
                source = Path(tmp) / 'mozes-live.db'
                if not source.is_file():
                    continue
                with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as conn:
                    if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError('monitor DB failed integrity check')
                shutil.copy2(source, destination)
                cache = Path(tmp) / 'http-cache'
                if cache.is_dir():
                    target = Path(os.environ.get('MOZES_HTTP_CACHE', '.monitor/http-cache'))
                    target.mkdir(parents=True, exist_ok=True)
                    # Do not trust arbitrary paths, symlinks or executable artifact content.
                    for p in cache.iterdir():
                        if p.is_file() and not p.is_symlink() and p.suffix in {'.json', '.body'}:
                            shutil.copy2(p, target / p.name)
                print(f'Restored monitor state from run {candidate}')
                return True
        except (subprocess.CalledProcessError, OSError, ValueError, sqlite3.Error) as exc:
            print(f'State restore unavailable for run {candidate}: {type(exc).__name__}')
    if run_id:
        raise RuntimeError('requested source artifact could not be restored')
    print('No reusable monitor artifact; initial bootstrap will be marked as missing live data')
    return False


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id')
    args = parser.parse_args()
    restore(args.run_id or os.environ.get('SOURCE_RUN_ID') or None)
