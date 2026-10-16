"""Offline serialized-owner pilot; all databases, sources and clocks are synthetic."""
import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import pytest

from mozes import db, prospective_capture as capture
from mozes.intelligence_store import ensure_schema, state_get
from mozes.radar import bootstrap_database
from prospective_claim_support import issue_permit


def api():
    from mozes import prospective_pilot
    return prospective_pilot


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_REPOSITORY', 'Mozes2024/mozes-biotech-intelligence')
    monkeypatch.setenv('GITHUB_WORKFLOW', 'lightweight-live-monitor')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setenv('GITHUB_RUN_ID', '100')
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '0')
    monkeypatch.delenv('MOZES_PROSPECTIVE_PREPARE', raising=False)
    monkeypatch.setattr(db, 'utcnow', lambda: '2026-10-15T09:00:00+00:00')
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-15T12:00:00+00:00')
    path = tmp_path / 'mozes-live.db'
    conn = db.connect(path)
    bootstrap_database(conn)
    ensure_schema(conn)
    event = next(e for e in db.load_events(conn, 'live') if e['id'] == 'PHAR-SNDA-LOWER-WEIGHT')
    db.add_event_source(conn, event['id'], 'https://example.org/source', 'company_ir',
                        url='https://example.org/source', published_at='2026-10-15T10:00:00Z',
                        statement='Issuer expects a Q4 2026 readout', retrieved_at='2026-10-15T10:01:00Z')
    monkeypatch.setenv('MOZES_PROSPECTIVE_EVENT_IDS', json.dumps([event['id']]))
    monkeypatch.setenv('MOZES_DB_PATH', str(path))
    yield path, conn, event
    conn.close()


def prepared(owner, monkeypatch):
    pilot = api()
    path, conn, event = owner
    conn.commit()
    proof = {'source_run_id': '99', 'db_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    monkeypatch.setenv('MOZES_PROSPECTIVE_PREPARE', json.dumps(proof))
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '1')  # initializer process only
    assert pilot.prepare(path)['status'] == 'PREPARED'
    monkeypatch.delenv('MOZES_PROSPECTIVE_PREPARE')
    issue_permit(monkeypatch, path.parent)
    return pilot, conn, event


def test_disabled_observer_does_not_access_the_database(monkeypatch):
    pilot = api()
    monkeypatch.delenv('MOZES_PROSPECTIVE_CAPTURE', raising=False)
    assert pilot.after_monitor(None, monitor_status='OK')['status'] == 'DISABLED'


def test_legacy_global_flag_without_claim_cannot_archive_or_capture(owner, monkeypatch):
    pilot, conn, _ = prepared(owner, monkeypatch)
    monkeypatch.delenv('MOZES_PROSPECTIVE_AUTHORIZATION', raising=False)
    monkeypatch.delenv('MOZES_PROSPECTIVE_CLAIM_SHA', raising=False)
    archives = list(conn.execute('SELECT * FROM source_archive'))
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report.get('captured', 0) == 0
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 0
    assert list(conn.execute('SELECT * FROM source_archive')) == archives


def test_two_sequential_runs_cannot_capture_under_one_authorization(owner, monkeypatch):
    pilot, conn, event = prepared(owner, monkeypatch)
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    # Real source changes must not turn a queued successor into another authorized run.
    db.add_event_source(conn, event['id'], 'https://example.org/second', 'company_ir',
        url='https://example.org/second', statement='A new issuer statement',
        published_at='2026-10-15T11:00:00Z', retrieved_at='2026-10-15T11:01:00Z')
    archives = list(conn.execute('SELECT * FROM source_archive'))
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report.get('captured', 0) == 0
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 1
    assert list(conn.execute('SELECT * FROM source_archive')) == archives


def test_capture_failure_cannot_reenter_archive_or_capture_in_same_run(owner, monkeypatch):
    pilot, conn, _ = prepared(owner, monkeypatch)
    attempts = []
    def crash(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('synthetic failure after source archiving')
    monkeypatch.setattr(capture, 'record', crash)
    assert pilot.after_monitor(conn, monitor_status='OK')['status'] == 'INCOMPLETE'
    pilot.after_monitor(conn, monitor_status='OK')
    assert len(attempts) == 1


def test_prepare_requires_matching_durable_backup_and_never_overwrites_old_data(owner, monkeypatch):
    pilot = api()
    path, conn, _ = owner
    before = list(conn.iterdump())
    monkeypatch.setenv('MOZES_PROSPECTIVE_PREPARE', json.dumps({'source_run_id': '99', 'db_sha256': '0' * 64}))
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '1')
    with pytest.raises(ValueError, match='backup_hash'):
        pilot.prepare(path)
    assert list(conn.iterdump()) == before
    pilot, conn, _ = prepared(owner, monkeypatch)
    assert pilot.verify(conn)['valid'] is True
    assert state_get(conn, 'prospective_pilot_preparation')['backup']['source_run_id'] == '99'


def test_monitor_capture_archives_excerpt_and_reentry_is_denied(owner, monkeypatch):
    pilot, conn, event = prepared(owner, monkeypatch)
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report['status'] == 'OK' and report['captured'] == 1
    assert pilot.after_monitor(conn, monitor_status='OK')['status'] == 'SKIPPED'
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 1
    row = capture.as_of(conn, event['id'], '2026-10-15T13:00:00Z')[0]
    assert row['eligible'] is False and row['executable'] is False
    claim = row['analysis']['prospective_pilot_context']['one_shot_claim']
    assert claim['record']['run_id'] == '100' and claim['record']['run_attempt'] == 1
    assert claim['record']['event_id'] == event['id']
    assert len(claim['claim_sha']) == 40
    assert row['sources'][0]['source_type'] == 'prospective_excerpt'
    source = db.source_archive_row(conn, row['sources'][0]['source_id'])
    assert source['content'] == 'Issuer expects a Q4 2026 readout'
    assert json.loads(source['metadata_json'])['upstream_retrieved_at_claimed'] == '2026-10-15T10:01:00Z'


def test_unchanged_tick_is_skipped_but_forecast_revision_is_captured(owner, monkeypatch):
    pilot, conn, event = prepared(owner, monkeypatch)
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    issue_permit(monkeypatch, owner[0].parent, run_id='101')
    assert pilot.after_monitor(conn, monitor_status='OK')['unchanged'] == 1
    row = conn.execute('SELECT payload FROM events WHERE id=?', (event['id'],)).fetchone()
    value = json.loads(row[0])
    value['chronology'].append({'date': '2026-10-15', 'date_text': 'Q1 2027', 'src': 'unknown', 'text': 'Revised forecast'})
    with conn:
        conn.execute('UPDATE events SET payload=? WHERE id=?', (json.dumps(value), event['id']))
    monkeypatch.setenv('GITHUB_RUN_ID', '102')
    issue_permit(monkeypatch, owner[0].parent, run_id='102')
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 2


@pytest.mark.parametrize('problem', ['unprepared', 'wrong_owner', 'monitor_failed', 'bad_allowlist'])
def test_unsafe_capture_is_skipped_without_changing_monitor_or_alerts(owner, monkeypatch, problem):
    pilot = api()
    _, conn, _ = owner
    if problem != 'unprepared':
        pilot, conn, _ = prepared(owner, monkeypatch)
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '1')
    if problem == 'wrong_owner': monkeypatch.setenv('GITHUB_REF', 'refs/heads/not-main')
    if problem == 'bad_allowlist': monkeypatch.setenv('MOZES_PROSPECTIVE_EVENT_IDS', '["A","B","C","D"]')
    before = list(conn.execute('SELECT * FROM alert_outbox'))
    report = pilot.after_monitor(conn, monitor_status='FAILED' if problem == 'monitor_failed' else 'OK')
    assert report['status'] in {'SKIPPED', 'INCOMPLETE'}
    assert list(conn.execute('SELECT * FROM alert_outbox')) == before
    assert db.validation_row(conn, 'runup')['enabled'] == 0


def test_capture_error_is_nonfatal_and_owned_archive_storage_is_bounded(owner, monkeypatch):
    pilot, conn, _ = prepared(owner, monkeypatch)
    monkeypatch.setattr(pilot, 'MAX_ARCHIVE_BYTES', 1)
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report['status'] == 'INCOMPLETE' and report['captured'] == 0
    assert conn.execute("SELECT count(*) FROM source_archive WHERE source_id LIKE 'PROSPECTIVE-SOURCE-%'").fetchone()[0] == 0


def test_checkpoint_copy_restore_and_disable_preserve_captures(owner, monkeypatch, tmp_path):
    pilot, conn, event = prepared(owner, monkeypatch)
    pilot.after_monitor(conn, monitor_status='OK')
    restored = tmp_path / 'restored.db'
    with sqlite3.connect(restored) as destination:
        conn.backup(destination)
    copy = sqlite3.connect(restored); copy.row_factory = sqlite3.Row
    assert pilot.verify(copy)['valid'] is True
    assert capture.as_of(copy, event['id'], '2026-10-15T13:00:00Z') == capture.as_of(conn, event['id'], '2026-10-15T13:00:00Z')
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '0')
    before = list(copy.iterdump())
    assert pilot.after_monitor(copy, monitor_status='OK')['status'] == 'DISABLED'
    assert list(copy.iterdump()) == before
    copy.close()


def test_checked_payload_binds_observation_key_to_row_metadata(owner, monkeypatch):
    _, conn, event = prepared(owner, monkeypatch)
    row = capture.record(conn, observation_key='key', analysis={'id': event['id'], 'ticker': event['ticker'],
        'type': event['type'], 'as_of': '2026-10-15', 'versions': {'model': '1'}},
        forecast={'origin': 'unknown', 'window': None}, source_ids=[], selection_rule={'id': 'test', 'definition': {}})
    conn.execute('DROP TRIGGER prospective_no_update')
    with conn:
        conn.execute("UPDATE prospective_evidence SET observation_key='different'")
    stored = conn.execute('SELECT * FROM prospective_evidence').fetchone()
    with pytest.raises(ValueError, match='integrity'):
        capture._checked(stored)


def test_prepare_failure_rolls_back_ddl_and_backup_is_restorable(owner, monkeypatch, tmp_path):
    pilot = api()
    path, conn, _ = owner
    before = list(conn.iterdump())
    backup = tmp_path / 'retained-backup.db'
    backup.write_bytes(path.read_bytes())
    proof = {'source_run_id': '99', 'db_sha256': pilot.file_hash(backup)}
    monkeypatch.setenv('MOZES_PROSPECTIVE_PREPARE', json.dumps(proof))
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '1')
    original = capture.ensure_schema
    def fail(connection):
        original(connection)
        raise ValueError('synthetic failure after DDL')
    monkeypatch.setattr(capture, 'ensure_schema', fail)
    with pytest.raises(ValueError, match='synthetic'):
        pilot.prepare(path)
    assert list(conn.iterdump()) == before
    with sqlite3.connect(backup) as restored:
        assert list(restored.iterdump()) == before
    monkeypatch.setattr(capture, 'ensure_schema', original)
    assert pilot.prepare(path)['status'] == 'PREPARED'
    after = list(conn.iterdump())
    assert pilot.prepare(path)['status'] == 'ALREADY_PREPARED'
    assert list(conn.iterdump()) == after
    assert pilot.file_hash(backup) == proof['db_sha256']


def test_caller_transaction_is_not_committed_and_broken_connection_is_nonfatal(owner, monkeypatch):
    pilot, conn, _ = prepared(owner, monkeypatch)
    conn.execute("INSERT INTO v2c_state VALUES('not_committed','{}')")
    assert pilot.after_monitor(conn, monitor_status='OK')['reason'] == 'caller_transaction_open'
    assert conn.in_transaction
    conn.rollback()
    broken = sqlite3.connect(':memory:'); broken.close()
    assert pilot.after_monitor(broken, monitor_status='OK')['health_persistence_failed'] is True


def test_heartbeat_and_explicit_daily_coverage_gap(owner, monkeypatch):
    pilot, conn, _ = prepared(owner, monkeypatch)
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-15T18:00:01+00:00')
    issue_permit(monkeypatch, owner[0].parent, run_id='101', now=capture.utcnow())
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    monkeypatch.setenv('GITHUB_RUN_ID', '102')
    issue_permit(monkeypatch, owner[0].parent, run_id='102', now=capture.utcnow())
    monkeypatch.setattr(pilot, 'MAX_DAILY_RECORDS', 2)
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report['status'] == 'INCOMPLETE' and report['skipped'] == 1
    assert report['reason'] == 'pilot_time_or_daily_budget'


@pytest.mark.parametrize('limit', ['time', 'sources', 'event', 'prices', 'ledger'])
def test_resource_limits_do_not_write_partial_capture(owner, monkeypatch, limit):
    pilot, conn, _ = prepared(owner, monkeypatch)
    if limit == 'time': monkeypatch.setattr(pilot, 'MAX_SECONDS', 0)
    if limit == 'sources': monkeypatch.setattr(pilot, 'MAX_SOURCES', 0)
    if limit == 'event': monkeypatch.setattr(pilot, 'MAX_EVENT_BYTES', 1)
    if limit == 'prices':
        db.store_prices(conn, owner[2]['ticker'], [{'date': '2026-10-14', 'close': 10}], 'synthetic')
        monkeypatch.setattr(pilot, 'MAX_PRICE_ROWS', 0)
    if limit == 'ledger': monkeypatch.setattr(capture, 'MAX_RECORDS', 0)
    before = list(conn.execute('SELECT * FROM alert_outbox'))
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report['status'] == 'INCOMPLETE'
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 0
    assert list(conn.execute('SELECT * FROM alert_outbox')) == before


def test_source_revision_and_integrity_check_preserve_previous_evidence(owner, monkeypatch):
    pilot, conn, event = prepared(owner, monkeypatch)
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    original = capture.as_of(conn, event['id'], '2026-10-15T13:00:00Z')[0]
    original_source = db.source_archive_row(conn, original['sources'][0]['source_id'])
    db.add_event_source(conn, event['id'], 'https://example.org/revision', 'company_ir',
        url='https://example.org/revision', statement='Updated primary-source statement',
        published_at='2026-10-15T11:00:00Z', retrieved_at='2026-10-15T11:01:00Z')
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    issue_permit(monkeypatch, owner[0].parent, run_id='101')
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    rows = capture.as_of(conn, event['id'], '2026-10-15T13:00:00Z')
    assert rows[0] == original and rows[1]['previous_capture_id'] == original['capture_id']
    assert db.source_archive_row(conn, original_source['source_id']) == original_source
    assert pilot.verify(conn)['records'] == 2
    # Synthetic privileged corruption; verification must detect content/hash mismatch.
    conn.execute('DROP TRIGGER trg_source_archive_no_update')
    with conn:
        conn.execute('UPDATE source_archive SET content=? WHERE source_id=?', ('corrupt', original_source['source_id']))
    with pytest.raises(ValueError, match='source_integrity'):
        pilot.verify(conn)


def test_missing_quote_is_explicit_and_future_source_cannot_be_certified(owner, monkeypatch):
    pilot, conn, event = prepared(owner, monkeypatch)
    with conn:
        conn.execute('UPDATE event_sources SET statement=NULL WHERE event_id=?', (event['id'],))
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report['captured'] == 1 and report['source_gaps'] == 1
    assert report['status'] == 'INCOMPLETE'
    assert state_get(conn, pilot.HEALTH)['status'] == 'INCOMPLETE'
    assert json.loads((owner[0].parent / 'prospective-pilot.json').read_text())['status'] == 'INCOMPLETE'
    saved = capture.as_of(conn, event['id'], '2026-10-15T13:00:00Z')
    assert saved[0]['analysis']['prospective_pilot_context']['source_scope'] == 'missing'
    assert pilot.after_monitor(conn, monitor_status='OK')['status'] == 'SKIPPED'
    assert capture.as_of(conn, event['id'], '2026-10-15T13:00:00Z') == saved
    monkeypatch.setenv('GITHUB_RUN_ID', '101')
    issue_permit(monkeypatch, owner[0].parent, run_id='101')
    with conn:
        conn.execute('UPDATE event_sources SET statement=?,retrieved_at=? WHERE event_id=?',
                     ('Future quote', '2026-10-16T12:00:00Z', event['id']))
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report['status'] == 'INCOMPLETE' and report['captured'] == 0


@pytest.mark.parametrize('failure', [None, 'observer', 'source_gaps', 'refused_export', 'unrelated_export'])
def test_real_monitor_hook_order_durability_and_failure_isolation(owner, monkeypatch, tmp_path, failure):
    from mozes import pipeline_v2c as pipeline, edge_enrichment
    from mozes.intelligence_store import state_put
    pilot, conn, event = prepared(owner, monkeypatch)
    if failure == 'source_gaps':
        with conn:
            conn.execute('UPDATE event_sources SET statement=NULL WHERE event_id=?', (event['id'],))
    path = owner[0]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, 'DB_PATH', path)
    monkeypatch.setenv('MOZES_RESULT_PATH', str(tmp_path / 'result.json'))
    def source_scan(connection):
        assert connection.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 0
        state_put(connection, 'synthetic_queued_alert', {'queued': True})
        return {'status': 'OK', 'details': {}}
    def export(connection, *args, **kwargs):
        assert state_get(connection, 'synthetic_queued_alert')['queued']
        assert connection.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == (0 if failure == 'observer' else 1)
        if failure == 'refused_export': raise ValueError('regulatory_review_public_byte_budget')
        if failure == 'unrelated_export': raise ValueError('unrelated_failure')
        return {'synthetic_public_payload': True}
    monkeypatch.setattr(pipeline, 'run_priority_pipeline', source_scan)
    monkeypatch.setattr(pipeline, 'export_payload', export)
    monkeypatch.setattr(pipeline, 'semantic_fingerprint', lambda value: 'fingerprint')
    monkeypatch.setattr(pipeline, 'publication_due', lambda *a, **k: False)
    monkeypatch.setattr(pipeline, 'trim_cache', lambda *a: None)
    if failure == 'observer':
        def fail(*a, **k): raise RuntimeError('synthetic observer defect')
        monkeypatch.setattr(pilot, 'after_monitor', fail)
    if failure == 'unrelated_export':
        with pytest.raises(ValueError, match='unrelated_failure'):
            pipeline.main(['monitor', '--priority-only'])
    else:
        assert pipeline.main(['monitor', '--priority-only']) == 0
        result = json.loads((tmp_path / 'result.json').read_text())
        assert result['status'] == 'OK' and result['manifest']['runtime_status'] == 'OK'
        assert result['details']['prospective_capture']['status'] == ('INCOMPLETE' if failure in {'observer', 'source_gaps'} else 'OK')
        assert result['manifest']['data_publishable'] is (failure != 'refused_export')
    receipt = edge_enrichment.checkpoint(path)
    assert receipt['db_sha256'] == pilot.file_hash(path)
    assert pilot.verify(conn)['records'] == (0 if failure == 'observer' else 1)


def test_real_public_export_never_exposes_private_capture(owner, monkeypatch, tmp_path):
    from datetime import datetime
    from mozes import payload_v3
    from mozes.pipeline_v2c import export_payload
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 15, 12, tzinfo=tz)
    monkeypatch.setattr(payload_v3, 'datetime', FixedDatetime)
    pilot, conn, _ = prepared(owner, monkeypatch)
    before = export_payload(conn, str(tmp_path / 'before.json'))
    alerts = list(conn.execute('SELECT * FROM alert_outbox'))
    gates = [dict(r) for r in conn.execute('SELECT * FROM model_validation')]
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    after = export_payload(conn, str(tmp_path / 'after.json'))
    assert before == after
    assert 'PROSPECTIVE-' not in json.dumps(after)
    assert list(conn.execute('SELECT * FROM alert_outbox')) == alerts
    assert [dict(r) for r in conn.execute('SELECT * FROM model_validation')] == gates


def test_prepared_captured_checkpoint_is_accepted_read_only_by_real_pages_validator(owner, monkeypatch):
    import importlib.util
    from mozes import edge_enrichment
    pilot, conn, _ = prepared(owner, monkeypatch)
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    path = owner[0]
    edge_enrichment.checkpoint(path)
    root = path.parent
    (root / 'manifest.json').write_text(json.dumps({'schema': 1, 'data_publishable': True,
        'source_sha': 'a' * 40, 'generated_at': '2026-10-15T08:59:00Z'}))
    run = {'id': 100, 'head_sha': 'a' * 40, 'created_at': '2026-10-15T08:58:00Z'}
    artifact = {'id': 77, 'created_at': '2026-10-15T12:01:00Z'}
    spec = importlib.util.spec_from_file_location('pilot_pages', Path(__file__).resolve().parents[1] / 'scripts/restore_pages_state.py')
    pages = importlib.util.module_from_spec(spec); spec.loader.exec_module(pages)
    before = path.read_bytes()
    source, receipt = pages.validate(root, run, artifact, datetime(2026, 10, 15, 12, 2, tzinfo=timezone.utc))
    assert source == path and receipt['source_run_id'] == '100'
    assert receipt['db_sha256'] == hashlib.sha256(before).hexdigest()
    assert path.read_bytes() == before and pilot.verify(conn)['records'] == 1


def test_workflow_retains_single_owner_and_prepare_precedes_all_writers():
    text = (Path(__file__).resolve().parents[1] / '.github/workflows/lightweight-monitor.yml').read_text()
    assert "MOZES_PROSPECTIVE_CAPTURE || '0'" in text
    assert "vars.MOZES_PROSPECTIVE_CAPTURE != '1'" in text
    assert text.index('id: restore') < text.index('prospective_pilot prepare') < text.index('python -m mozes.edge_sync')
    assert 'group: mozes-hot-monitor' in text and 'cancel-in-progress: false' in text
    assert text.count('python -m mozes.prospective_pilot prepare') == 1
    assert '.monitor/prospective-pilot.json' in text


def test_all_existing_database_rows_are_preserved_by_prepare_and_capture(owner, monkeypatch):
    _, conn, _ = owner
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    before = {table: set(tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')) for table in tables}
    pilot, conn, _ = prepared(owner, monkeypatch)
    assert pilot.after_monitor(conn, monitor_status='OK')['captured'] == 1
    for table, rows in before.items():
        assert rows <= set(tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')), table


@pytest.mark.parametrize('mode', ['off', 'export'])
def test_off_or_export_mode_never_invokes_observer(owner, monkeypatch, tmp_path, mode):
    from mozes import pipeline_v2c as pipeline
    pilot = api()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, 'DB_PATH', owner[0])
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '0' if mode == 'off' else '1')
    def forbidden(*a, **k): raise AssertionError('observer must never be invoked')
    monkeypatch.setattr(pilot, 'after_monitor', forbidden)
    monkeypatch.setattr(pipeline, 'run_priority_pipeline', lambda c: {'status': 'OK', 'details': {}})
    monkeypatch.setattr(pipeline, 'export_payload', lambda *a, **k: {})
    monkeypatch.setattr(pipeline, 'semantic_fingerprint', lambda value: 'fingerprint')
    monkeypatch.setattr(pipeline, 'publication_due', lambda *a, **k: False)
    monkeypatch.setattr(pipeline, 'trim_cache', lambda *a: None)
    monkeypatch.setattr(pipeline, 'reconcile_catalog', lambda *a: None)
    monkeypatch.setattr(pipeline, 'import_local_research', lambda *a: None)
    assert pipeline.main(['monitor', '--priority-only'] if mode == 'off' else ['export']) == 0
    assert not owner[1].execute("SELECT 1 FROM sqlite_master WHERE name='prospective_evidence'").fetchone()


def test_unsafe_url_without_excerpt_is_not_written_to_private_capture(owner, monkeypatch):
    pilot, conn, event = prepared(owner, monkeypatch)
    with conn:
        conn.execute('UPDATE event_sources SET url=?,statement=NULL WHERE event_id=?',
                     ('https://example.org/source?token=synthetic-not-a-secret', event['id']))
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report['status'] == 'INCOMPLETE' and report['captured'] == 0
    assert 'synthetic-not-a-secret' not in json.dumps(report)


def test_terminal_event_is_skipped_instead_of_archiving_outcome(owner, monkeypatch):
    pilot, conn, event = prepared(owner, monkeypatch)
    db.upsert_event_state(conn, event['id'], 'APPROVED')
    report = pilot.after_monitor(conn, monitor_status='OK')
    assert report['skipped'] == 1 and report['captured'] == 0
    assert conn.execute("SELECT count(*) FROM source_archive WHERE source_id LIKE 'PROSPECTIVE-SOURCE-%'").fetchone()[0] == 0
