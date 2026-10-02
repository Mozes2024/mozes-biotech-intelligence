"""Single-owner data production; offline Pages export never scans public sources."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from . import db
from .config import DB_PATH
from .data_loader import load_live, load_sources
from .source_observability import observed
from .intelligence_store import (digest, encode, ensure_schema, finalize_owned_runs,
                                 operation_finish, operation_start, state_get, state_put, utcnow)

DATA = Path(__file__).parent / 'data'


def validate_child_result(path, run_token, returncode):
    """Validate the atomic child result; stdout and stale files are not a contract."""
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, TypeError, ValueError):
        return None, 'missing_or_malformed_result'
    if value.get('protocol') != 'mozes-v2e2-result' or value.get('run_token') != run_token:
        return None, 'wrong_result_owner_or_protocol'
    status = value.get('status')
    if returncode == 0 and status not in {'OK', 'INCOMPLETE', 'BOUNDED'}:
        return None, 'contradictory_success_result'
    if returncode != 0 and status not in {'FAILED', 'PARTIAL'}:
        return None, 'contradictory_failure_result'
    return value, None


def reconcile_catalog(conn):
    from .radar import bootstrap_database
    bootstrap_database(conn)
    ensure_schema(conn)
    incoming = {row['id']: row for row in load_live()}
    prior = state_get(conn, 'curated_catalog', {})
    existing = {row['id']: row for row in db.load_events(conn, 'live')}
    changes, conflicts = [], []
    with conn:
        for event_id, item in incoming.items():
            if event_id not in existing:
                conn.execute("INSERT INTO events(id,kind,payload) VALUES(?,'live',?)", (event_id, encode(item)))
                changes.append(event_id)
            elif event_id in prior and prior[event_id] != item:
                # Three-way merge: never overwrite runtime discoveries or terminal state.
                live, old = existing[event_id], prior[event_id]
                for key, value in item.items():
                    if old.get(key) == value:
                        continue
                    if key == 'chronology':
                        sequence = list(live.get(key) or [])
                        seen = {encode(x) for x in sequence}
                        sequence.extend(x for x in value if encode(x) not in seen)
                        live[key] = sequence
                    elif live.get(key) == old.get(key):
                        live[key] = value
                    else:
                        conflicts.append({'event_id': event_id, 'field': key})
                conn.execute('UPDATE events SET payload=? WHERE id=?', (encode(live), event_id))
                changes.append(event_id)
        for source in load_sources().values():
            conn.execute('INSERT INTO sources(id,url,source_type,reliability,published,retrieved,title) VALUES(?,?,?,?,?,?,?) '
                         'ON CONFLICT(id) DO UPDATE SET url=excluded.url,source_type=excluded.source_type,reliability=excluded.reliability,'
                         'published=excluded.published,retrieved=excluded.retrieved,title=excluded.title',
                         tuple(source.get(k) for k in ('id', 'url', 'source_type', 'reliability', 'published', 'retrieved', 'title')))
    state_put(conn, 'curated_catalog', incoming)
    bootstrap_database(conn)  # creates state only for newly added events; does not reset existing state
    return {'changed': changes, 'conflicts': conflicts}


def import_local_research(conn):
    from .historical import backfill_prices, import_bundle, readiness_summary
    from .historical_batch import import_checked_batch, BUNDLE
    from .radar import validation_status
    files = sorted([p for p in DATA.rglob('*') if p.is_file() and
                    (p.suffix == '.csv' or p.name.startswith('historical') or p.name == 'manifest.json')])
    fingerprint = digest([(str(p.relative_to(DATA)), hashlib.sha256(p.read_bytes()).hexdigest()) for p in files])
    if state_get(conn, 'research_fingerprint') == fingerprint:
        return {'status': 'unchanged', 'fingerprint': fingerprint}
    before_gates = validation_status(conn)
    old_prices = defaultdict(list)
    for row in conn.execute('SELECT ticker,date,close,volume,source FROM prices'):
        record = dict(row)
        old_prices[(record['ticker'], record['source'])].append(record)
    starter = json.loads((DATA / 'historical_starter.json').read_text(encoding='utf-8'))
    batch = json.loads((DATA / BUNDLE).read_text(encoding='utf-8'))
    expected = {x['case_id'] for x in starter['cases'] + batch['cases']}
    try:
        import_bundle(conn, DATA / 'historical_starter.json')
        for case in starter['cases']:
            backfill_prices(conn, case['case_id'], 'csv',
                            stock_file=str(DATA / 'starter_prices' / (case['ticker'] + '.csv')),
                            benchmark_file=str(DATA / 'starter_prices' / 'XBI.csv'),
                            source_url='https://query1.finance.yahoo.com/v8/finance/chart/')
        import_checked_batch(conn)
    finally:
        # Historical imports use shared prices as a staging table. Do not let an old
        # capture overwrite newer market observations. Frozen case rows remain untouched.
        for (ticker, source), rows in old_prices.items():
            db.store_prices(conn, ticker, rows, source)
    found = {r[0] for r in conn.execute('SELECT case_id FROM historical_cases WHERE legacy_post_hoc=0')}
    if not expected <= found:
        raise ValueError('local research import lost canonical case IDs')
    if validation_status(conn) != before_gates:
        raise ValueError('research import must not change validation gates')
    state_put(conn, 'research_fingerprint', fingerprint)
    ready = readiness_summary(conn)
    return {'status': 'imported', 'expected_cases': len(expected), 'fingerprint': fingerprint,
            'research_ready': ready['research_ready'], 'runup_ready': ready['runup_ready'], 'hold_ready': ready['hold_ready']}


@observed
def identity_audit(conn):
    from .ingest.nasdaq_trader import fetch_current_listings
    from .ingest.edgar import fetch_company_ticker_map
    from .entity_resolution import update_company_map
    from .security import audit_watch_universe
    rid = operation_start(conn, 'identity')
    try:
        listings = fetch_current_listings()
        audited = audit_watch_universe(conn, listings, provider='nasdaq')
        result = {'audited': len(audited), 'sec_configured': bool(os.environ.get('SEC_USER_AGENT'))}
        if os.environ.get('SEC_USER_AGENT'):
            mapped = update_company_map(conn, fetch_company_ticker_map(), listings)
            result.update(mapped=len(mapped['projection']), ambiguous=len(mapped['ambiguous']))
        else:
            result['sec_skipped'] = 'SEC_USER_AGENT missing'
        status = 'OK' if result['sec_configured'] else 'PARTIAL'
    except Exception as exc:
        result, status = {'error': str(exc)[:200]}, 'FAILED'
    operation_finish(conn, rid, status, result)
    return {**result, 'status': status}


def semantic_fingerprint(payload):
    live = []
    for row in sorted(payload.get('live', []), key=lambda r: r['id']):
        financial = row.get('financial_context') or {}
        live.append({'id': row['id'], 'date': (row.get('date') or {}).get('window'),
                     'timing': [row.get(k) for k in ('timing_mode', 'trigger_current', 'trigger_target', 'trigger_as_of', 'monitoring_state')],
                     'recommendation': row.get('recommendation'), 'security': (row.get('security') or {}).get('status'),
                     'close': (row.get('market') or {}).get('last_close'),
                     'financial_source': financial.get('source_hash'), 'runway_band': financial.get('runway_band'),
                     'financing': [(x.get('accession'), x.get('kind')) for x in row.get('financing_events', [])]})
    health = payload.get('health') or {}
    return digest({'live': live, 'summary': payload.get('summary'),
                   'health': {'status': health.get('status'), 'warnings': health.get('warnings')},
                   'change_cursor': (payload.get('build') or {}).get('change_cursor'),
                   'v2c_statuses': {k: v.get('status') for k, v in (payload.get('intelligence_v2c') or {}).get('modules', {}).items() if k != 'pipeline'}})


def export_payload(conn, destination):
    from .payload_v3 import build
    payload = build(conn, date.today())
    payload['build'] = {'extension': 'v2E', 'source_sha': os.environ.get('GITHUB_SHA'),
                        'source_of_truth': 'runtime_export_not_checked_in_web_data_json',
                        'change_cursor': None}
    latest_change = conn.execute('SELECT change_id,detected_at FROM change_events ORDER BY detected_at DESC,change_id DESC LIMIT 1').fetchone()
    if latest_change:
        payload['build']['change_cursor'] = dict(latest_change)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return payload


def trim_cache(root, max_bytes=8 * 1024 * 1024):
    root = Path(root)
    files = sorted(root.glob('*'), key=lambda p: p.stat().st_mtime, reverse=True) if root.exists() else []
    size = 0
    for f in files:
        if not f.is_file():
            continue
        size += f.stat().st_size
        if size > max_bytes:
            f.unlink()


def run_pipeline(conn, *, deep=False):
    from .financial_intelligence import refresh_financials
    from .financing_intelligence import refresh_financing
    from .live_monitor import run_monitor
    from .live_prices import refresh_live_prices
    from .regulatory_lifecycle import quarantine_unbound_auto_events
    os.environ.setdefault('MOZES_RUN_TOKEN', uuid.uuid4().hex)
    rid = operation_start(conn, 'pipeline', owner_token=os.environ['MOZES_RUN_TOKEN'])
    details = {'catalog': reconcile_catalog(conn), 'research': import_local_research(conn)}
    details['identity'] = identity_audit(conn)
    details['quarantined_unbound'] = quarantine_unbound_auto_events(conn)
    if deep:
        deep_id = operation_start(conn, 'deep_refresh', owner_token=os.environ['MOZES_RUN_TOKEN'])
        # Subprocess has a hard wall-time budget. A timeout is recorded as incomplete,
        # not disguised as a successful full scan. Cached documents survive for next time.
        conn.commit()
        cmd = [sys.executable, '-m', 'mozes', 'refresh-v2', '--months', '9', '--no-sec-map']
        if not os.environ.get('SEC_USER_AGENT'):
            cmd = [sys.executable, '-m', 'mozes', 'discover-v2', '--months', '9']
        result_path = Path(os.environ.get('MOZES_RESULT_PATH') or (Path('.monitor') / ('result-' + os.environ['MOZES_RUN_TOKEN'] + '.json')))
        child_env = os.environ.copy()
        child_env['MOZES_PARENT_TOKEN'] = os.environ['MOZES_RUN_TOKEN']
        child_env['MOZES_RESULT_PATH'] = str(result_path)
        try:
            completed = subprocess.run(cmd, timeout=480, check=False, capture_output=True, text=True, env=child_env)
            rc = completed.returncode
            report = {'returncode': rc}
            child, result_error = validate_child_result(result_path, os.environ['MOZES_RUN_TOKEN'], rc)
            valid = child is not None
            if valid:
                report.update(child.get('details') or {})
                report['child_status'] = child.get('status')
            status = child.get('status') if valid else 'FAILED'
            if not valid:
                report['termination_reason'] = result_error or 'missing_or_invalid_child_result'
        except subprocess.TimeoutExpired:
            status, report = 'FAILED', {'termination_reason': 'parent_timeout_480_seconds'}
            finalize_owned_runs(conn, os.environ['MOZES_RUN_TOKEN'], 'FAILED', report['termination_reason'])
        operation_finish(conn, deep_id, status, report)
        details['deep_refresh'] = {**report, 'status': status}
        if report.get('coverage_audit'):
            details['coverage_audit'] = report['coverage_audit']
        else:
            from .coverage import audit_coverage
            details['coverage_audit'] = audit_coverage(conn)
    price_id = operation_start(conn, 'prices')
    try:
        details['prices'] = refresh_live_prices(conn)
    except Exception as exc:
        details['prices'] = {'status': 'FAILED', 'best_effort': True, 'error': type(exc).__name__}
    operation_finish(conn, price_id, details['prices'].get('status', 'OK'), details['prices'])
    details['monitor'] = run_monitor(conn, audit=False, sec=False)
    from .engine_v2 import score_event
    from .radar import live_event_records
    from .paper import PaperBook
    details['forward_candidates_added'] = PaperBook(conn).queue_candidates(
        [score_event(conn, event, date.today().isoformat()) for event in live_event_records(conn)])
    if os.environ.get('SEC_USER_AGENT'):
        details['financing'] = refresh_financing(conn)
        details['financials'] = refresh_financials(conn)
    else:
        for name in ('financing', 'financials'):
            module_run = operation_start(conn, name)
            operation_finish(conn, module_run, 'SKIPPED', {'reason': 'SEC_USER_AGENT missing'})
        details['sec_skipped'] = 'SEC_USER_AGENT missing'
    fatal = [k for k, value in details.items() if isinstance(value, dict) and
             (value.get('status') in {'FAILED', 'PARTIAL'} or value.get('errors') or value.get('error') or value.get('conflicts'))]
    bounded = [k for k, value in details.items() if isinstance(value, dict) and
               value.get('status') in {'INCOMPLETE', 'BOUNDED'}]
    status = 'PARTIAL' if fatal else 'INCOMPLETE' if bounded else 'OK'
    operation_finish(conn, rid, status, details)
    return {'status': status, 'details': details}


def main(argv=None):
    parser = argparse.ArgumentParser(description='v2C single-owner monitor and offline publication')
    parser.add_argument('mode', choices=['monitor', 'export'])
    parser.add_argument('--deep', action='store_true')
    parser.add_argument('--output', default='web/data.json')
    args = parser.parse_args(argv)
    conn = db.connect(DB_PATH)
    ensure_schema(conn)
    if args.mode == 'export':
        reconcile_catalog(conn)
        import_local_research(conn)
        export_payload(conn, args.output)
        conn.close()
        return 0
    result = run_pipeline(conn, deep=args.deep)
    payload = export_payload(conn, args.output)
    fingerprint = semantic_fingerprint(payload)
    previous = state_get(conn, 'publish_request', {})
    now = datetime.now(timezone.utc)
    try:
        age = (now - datetime.fromisoformat(previous['at'])).total_seconds()
    except (KeyError, ValueError):
        age = 1e9
    should_publish = fingerprint != previous.get('fingerprint') or age >= 86400
    if should_publish:
        state_put(conn, 'publish_request', {'fingerprint': fingerprint, 'at': now.isoformat()})
    manifest = {'schema': 1, 'pipeline_complete': True, 'should_publish': should_publish,
                'fingerprint': fingerprint, 'source_sha': os.environ.get('GITHUB_SHA'), 'generated_at': utcnow()}
    Path('.monitor').mkdir(exist_ok=True)
    Path('.monitor/manifest.json').write_text(encode(manifest), encoding='utf-8')
    result_path = os.environ.get('MOZES_RESULT_PATH')
    if result_path:
        tmp = Path(result_path + '.tmp')
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(encode({'protocol': 'mozes-v2e2-result', 'run_token': os.environ.get('MOZES_RUN_TOKEN'),
                               'status': result.get('status'), 'details': result.get('details'),
                               'manifest': manifest}), encoding='utf-8')
        os.replace(tmp, result_path)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as fh:
            fh.write(f"should_publish={'true' if should_publish else 'false'}\n")
    trim_cache(os.environ.get('MOZES_HTTP_CACHE', '.monitor/http-cache'))
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
