"""Offline forward-observation contracts; no source/monitor/production dispatch."""
import json
import sqlite3
from datetime import date

import pytest

from mozes import db
from mozes.radar import bootstrap_database
from mozes.versions import VERSIONS


def api():
    from mozes import prospective_capture
    return prospective_capture


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    capture = api()
    conn = db.connect(tmp_path / 'synthetic.db')
    monkeypatch.setattr(db, 'utcnow', lambda: '2026-10-15T09:00:00+00:00')
    bootstrap_database(conn)
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '1')
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-15T12:00:00+00:00')
    db.archive_source(conn, 'original', 'https://example.org/original', 'company_ir',
                      '2026-10-15T10:00:00Z', '2026-10-15T10:01:00Z', 'Original Q4 forecast')
    capture.ensure_schema(conn)
    yield capture, conn
    conn.close()


def analysis(**changes):
    row = dict(id='EVENT-1', ticker='TEST', type='P3_TOPLINE', as_of='2026-10-15',
               versions=dict(VERSIONS), features={'trial_phase': 3},
               classification={'class': 'WATCH'})
    row.update(changes)
    return row


def observe(capture, conn, key='first', **changes):
    fields = dict(observation_key=key, analysis=analysis(), source_ids=['original'],
                  forecast={'origin': 'issuer_announced', 'window': {'start': '2026-10-01',
                  'end': '2026-12-31', 'precision': 'quarter'}, 'created_at': None,
                  'discovered_at': None, 'modified_at': None, 'available_at': None},
                  selection_rule={'id': 'research-observation-v1', 'definition': {'include': 'all_reviewed'}},
                  code_sha='828245bab4981dd66e0597d2d0d653af74f0f247')
    fields.update(changes)
    return capture.record(conn, **fields)


def test_disabled_capture_does_not_read_or_modify_database(monkeypatch):
    capture = api()
    monkeypatch.delenv('MOZES_PROSPECTIVE_CAPTURE', raising=False)
    conn = sqlite3.connect(':memory:')
    with pytest.raises(capture.Disabled):
        capture.ensure_schema(conn)
    with pytest.raises(capture.Disabled):
        observe(capture, conn)
    assert list(conn.execute("SELECT name FROM sqlite_master")) == []


def test_forecast_revisions_preserve_first_observation_and_cutoff(ledger, monkeypatch):
    capture, conn = ledger
    first = observe(capture, conn)
    frozen = json.dumps(first, sort_keys=True)
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-16T12:00:00+00:00')
    revision = {'origin': 'inferred_estimate', 'window': {'start': '2027-01-01',
        'end': '2027-03-31', 'precision': 'quarter'}, 'created_at': None,
        'discovered_at': None, 'modified_at': None, 'available_at': None}
    second = observe(capture, conn, 'revision', forecast=revision, analysis=analysis(as_of='2026-10-16'))
    assert first['capture_id'] != second['capture_id']
    earlier = capture.as_of(conn, 'EVENT-1', '2026-10-15T13:00:00Z')
    assert len(earlier) == 1 and json.dumps(earlier[0], sort_keys=True) == frozen
    assert earlier[0]['forecast']['window']['end'] == '2026-12-31'
    assert second['forecast']['origin'] == 'inferred_estimate'


def test_retries_are_idempotent_conflicts_rejected(ledger, monkeypatch):
    capture, conn = ledger
    first = observe(capture, conn)
    before = list(conn.iterdump())
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-15T12:01:00+00:00')
    assert observe(capture, conn) == first
    assert list(conn.iterdump()) == before
    with pytest.raises(capture.Conflict):
        observe(capture, conn, analysis=analysis(features={'trial_phase': 2}))
    assert list(conn.iterdump()) == before


def test_unknown_provenance_and_availability_stay_unknown(ledger):
    capture, conn = ledger
    row = observe(capture, conn, source_ids=['missing-source'], code_sha=None,
                  analysis=analysis(versions={}), forecast={'origin': 'unknown', 'window': None})
    assert row['eligible'] is False and row['executable'] is False
    assert 'source_archive_missing:missing-source' in row['exclusions']
    assert 'forecast_unknown' in row['exclusions']
    assert row['forecast']['window'] is None
    assert row['price_reference'] == {'status': 'unknown'}
    assert row['availability']['historical_first_available_at'] is None
    assert row['availability']['observed_by_bio_at'] == row['captured_at']


def test_later_sources_or_outcomes_cannot_enter_an_earlier_capture(ledger):
    capture, conn = ledger
    db.archive_source(conn, 'future', 'https://example.org/future', 'company_ir',
                      '2026-10-16T10:00:00Z', '2026-10-16T10:01:00Z', 'Post-entry revision')
    before = list(conn.iterdump())
    with pytest.raises(ValueError, match='future_source'):
        observe(capture, conn, source_ids=['future'])
    with pytest.raises(ValueError, match='outcome'):
        observe(capture, conn, analysis=analysis(features={'nested': {'actual_move': .5}}))
    assert list(conn.iterdump()) == before


def test_capture_does_not_accept_a_backdated_analysis(ledger):
    capture, conn = ledger
    with pytest.raises(ValueError, match='historical_capture_unsupported'):
        observe(capture, conn, analysis=analysis(as_of='2025-01-01'))
    assert capture.as_of(conn, 'EVENT-1', '2025-01-01T00:00:00Z') == []


def test_source_revisions_do_not_replace_original_source_or_snapshot(ledger):
    capture, conn = ledger
    first = observe(capture, conn)
    with pytest.raises(ValueError, match='different content'):
        db.archive_source(conn, 'original', 'https://example.org/original', 'company_ir',
                          '2026-10-15T10:00:00Z', '2026-10-15T10:01:00Z', 'Changed forecast')
    assert capture.as_of(conn, 'EVENT-1', '2026-10-15T13:00:00Z') == [first]


def test_price_reference_cannot_replace_an_older_vintage_or_certify_execution(ledger):
    capture, conn = ledger
    ref = {'status': 'unverified_reference', 'schema': 'mozes-price-staging-manifest-v1',
           'attachment_id': 'future-attachment', 'manifest_hash': 'a' * 64}
    first = observe(capture, conn, price_reference=ref)
    with pytest.raises(capture.Conflict):
        observe(capture, conn, price_reference={**ref, 'manifest_hash': 'b' * 64})
    assert first['eligible'] is False
    assert 'price_provenance_unverified_META_M8' in first['exclusions']
    assert first['price_reference'] == ref


def test_all_existing_data_and_gate_rows_are_preserved(ledger):
    capture, conn = ledger
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if r[0] not in {'prospective_evidence', 'sqlite_sequence'}]
    before = {t: list(conn.execute('SELECT * FROM ' + t)) for t in tables}
    row = observe(capture, conn, analysis=analysis(classification={'class': 'RUNUP', 'eligible': True}))
    assert row['eligible'] is False and row['executable'] is False
    for t, rows in before.items():
        assert list(conn.execute('SELECT * FROM ' + t)) == rows
    assert not db.validation_row(conn, 'runup')['enabled']
    assert not db.validation_row(conn, 'hold_through')['enabled']


def test_append_only_sql_and_storage_budgets(ledger, monkeypatch):
    capture, conn = ledger
    observe(capture, conn)
    for sql in ('UPDATE prospective_evidence SET payload=payload', 'DELETE FROM prospective_evidence',
                'INSERT OR REPLACE INTO prospective_evidence SELECT * FROM prospective_evidence'):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)
    before = list(conn.iterdump())
    monkeypatch.setattr(capture, 'MAX_RECORDS', 1)
    with pytest.raises(ValueError, match='storage_budget'):
        observe(capture, conn, 'second')
    assert list(conn.iterdump()) == before


def test_retry_after_day_and_gate_metadata_change_returns_original_capture(ledger, monkeypatch):
    capture, conn = ledger
    first = observe(capture, conn)
    db.set_validation(conn, 'runup', False, 0, 150, {'new_reporting_metric': 1})
    before = list(conn.iterdump())
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-16T12:00:00+00:00')
    assert observe(capture, conn) == first
    assert list(conn.iterdump()) == before
    with pytest.raises(ValueError, match='historical_capture_unsupported'):
        observe(capture, conn, 'new-key-with-old-analysis')


@pytest.mark.parametrize('session', ['premarket', 'intraday', 'afterhours', 'unknown'])
def test_declared_announcement_session_is_preserved_without_fabricated_fills(ledger, session):
    capture, conn = ledger
    row = observe(capture, conn, announcement_session=session)
    assert row['announcement_session'] == session
    assert row['execution'] == {'status': 'unobserved', 'actual_fills': None}
    assert not row['eligible'] and not row['executable']


@pytest.mark.parametrize('field', ['published_at', 'retrieved_at'])
def test_missing_source_timestamps_are_retained_and_fail_closed(ledger, field):
    capture, conn = ledger
    db.archive_source(conn, 'unknown-time', 'https://example.org/unknown', 'company_ir',
                      None if field == 'published_at' else '2026-10-15T10:00:00Z',
                      '' if field == 'retrieved_at' else '2026-10-15T10:01:00Z', 'Unknown availability')
    row = observe(capture, conn, source_ids=['unknown-time'])
    assert 'source_timestamp_unknown:unknown-time:' + field in row['exclusions']
    assert row['availability']['historical_first_available_at'] is None
    assert row['eligible'] is False


@pytest.mark.parametrize('as_of', ['2026-10-15T12:00:00', '2026-10-15T12:01:00Z',
                                 '2026-10-15T11:54:59Z', 'bad'])
def test_unclear_future_or_stale_decision_times_cannot_be_new_captures(ledger, as_of):
    capture, conn = ledger
    before = list(conn.iterdump())
    with pytest.raises(ValueError):
        observe(capture, conn, analysis=analysis(as_of=as_of))
    assert list(conn.iterdump()) == before


def test_current_aware_decision_time_remains_observation_only(ledger):
    capture, conn = ledger
    row = observe(capture, conn, analysis=analysis(as_of='2026-10-15T11:59:00Z'))
    assert 'analysis_time_precision_insufficient' not in row['exclusions']
    assert row['eligible'] is False and row['authoritative'] is False


@pytest.mark.parametrize('budget,reason', [('MAX_RECORD_BYTES', 'capture_byte_budget'),
                                         ('MAX_TOTAL_BYTES', 'storage_budget')])
def test_byte_budgets_fail_without_losing_existing_captures(ledger, monkeypatch, budget, reason):
    capture, conn = ledger
    first = observe(capture, conn)
    before = list(conn.iterdump())
    monkeypatch.setattr(capture, budget, 1)
    with pytest.raises(ValueError, match=reason):
        observe(capture, conn, 'over-budget')
    assert list(conn.iterdump()) == before
    assert capture.as_of(conn, 'EVENT-1', '2026-10-15T13:00:00Z') == [first]


def test_caller_transaction_is_not_committed(ledger):
    capture, conn = ledger
    conn.execute('BEGIN')
    observe(capture, conn)
    assert conn.in_transaction
    conn.rollback()
    assert capture.as_of(conn, 'EVENT-1', '2026-10-15T13:00:00Z') == []


def test_price_values_and_claimed_verified_manifests_are_not_accepted(ledger):
    capture, conn = ledger
    with pytest.raises(ValueError, match='unsupported_price_reference'):
        observe(capture, conn, price_reference={'status': 'verified', 'prices': [100, 101]})
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 0


def test_secret_fields_in_selection_definition_are_rejected(ledger):
    capture, conn = ledger
    with pytest.raises(ValueError, match='secret_fields'):
        observe(capture, conn, selection_rule={'id': 'rule', 'definition': {'api_key': 'synthetic-only'}})


def test_replacement_cannot_bypass_unique_observation_key(ledger):
    capture, conn = ledger
    observe(capture, conn)
    row = conn.execute('SELECT * FROM prospective_evidence').fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT OR REPLACE INTO prospective_evidence VALUES(?,?,?,?,?,?,?)',
                     ('other-capture-id', row['event_id'], row['observation_key'], row['captured_at'],
                      row['input_hash'], row['payload_hash'], row['payload']))


def test_capture_can_be_disabled_while_retained_records_stay_readable(ledger, monkeypatch):
    capture, conn = ledger
    row = observe(capture, conn)
    monkeypatch.delenv('MOZES_PROSPECTIVE_CAPTURE', raising=False)
    with pytest.raises(capture.Disabled):
        observe(capture, conn, 'disabled')
    assert capture.as_of(conn, 'EVENT-1', '2026-10-15T13:00:00Z') == [row]


@pytest.mark.parametrize('extra', [
    {'features': {'features_as_of': '2026-10-16'}},
    {'features': {'source': {'published_at': '2026-10-16T10:00:00Z'}}},
    {'market': {'last_close': {'date': '2026-10-16', 'close': 100}}},
    {'prices': [{'date': '2026-10-15', 'close': 100}]},
])
def test_future_feature_clocks_prices_and_inline_price_storage_are_rejected(ledger, extra):
    capture, conn = ledger
    with pytest.raises(ValueError):
        observe(capture, conn, analysis=analysis(**extra))
    assert conn.execute('SELECT count(*) FROM prospective_evidence').fetchone()[0] == 0


def test_future_forecast_window_is_not_confused_with_future_knowledge_time(ledger):
    capture, conn = ledger
    row = observe(capture, conn, forecast={'origin': 'issuer_announced',
        'window': {'start': '2027-01-01', 'end': '2027-03-31', 'precision': 'quarter'}})
    assert row['forecast']['window']['start'] == '2027-01-01'
    assert row['eligible'] is False


def test_new_table_does_not_enter_the_public_payload(ledger):
    from mozes.payload_v3 import build
    capture, conn = ledger
    observe(capture, conn, analysis=analysis(features={'private_capture_marker': 'not-public'}))
    public = json.dumps(build(conn, date(2026, 10, 15)), default=str)
    assert 'private_capture_marker' not in public
    assert conn.execute('SELECT count(*) FROM paper_signals').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM forward_candidates').fetchone()[0] == 0


def test_schema_requires_explicit_initialization_even_when_capture_enabled(tmp_path, monkeypatch):
    capture = api()
    conn = db.connect(tmp_path / 'uninitialized.db')
    monkeypatch.setenv('MOZES_PROSPECTIVE_CAPTURE', '1')
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-15T12:00:00Z')
    before = list(conn.iterdump())
    with pytest.raises(ValueError, match='schema_not_initialized'):
        observe(capture, conn)
    assert list(conn.iterdump()) == before


def test_return_to_an_earlier_forecast_is_still_a_distinct_revision(ledger, monkeypatch):
    capture, conn = ledger
    a = observe(capture, conn)
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-15T12:01:00Z')
    b = observe(capture, conn, 'B', forecast={'origin': 'issuer_announced',
                'window': {'start': '2027-01-01', 'end': '2027-03-31', 'precision': 'quarter'}})
    monkeypatch.setattr(capture, 'utcnow', lambda: '2026-10-15T12:02:00Z')
    a2 = observe(capture, conn, 'A-again')
    assert a2['previous_capture_id'] == b['capture_id']
    assert b['previous_capture_id'] == a['capture_id']
    assert a2['capture_id'] != a['capture_id']
    assert a2['forecast'] == a['forecast']
    assert len(capture.as_of(conn, 'EVENT-1', '2026-10-15T13:00:00Z')) == 3


@pytest.mark.parametrize('cutoff', ['2026-10-15', '2026-10-15T12:00:00', 'bad'])
def test_read_cutoffs_require_unambiguous_timestamps(ledger, cutoff):
    capture, conn = ledger
    observe(capture, conn)
    with pytest.raises(ValueError):
        capture.as_of(conn, 'EVENT-1', cutoff)


def test_corrupted_payload_fails_closed_instead_of_silently_hiding_a_record(ledger):
    capture, conn = ledger
    observe(capture, conn)
    # Simulate privileged corruption only in the synthetic fixture.
    conn.execute('DROP TRIGGER prospective_no_update')
    with conn:
        conn.execute("UPDATE prospective_evidence SET payload=json_set(payload,'$.eligible',1)")
    with pytest.raises(ValueError, match='integrity_failure'):
        capture.as_of(conn, 'EVENT-1', '2026-10-15T13:00:00Z')


@pytest.mark.parametrize('late_fact', ['publication', 'retrieval', 'forecast', 'gate'])
def test_capture_lag_cannot_borrow_evidence_after_the_aware_decision(ledger, late_fact):
    capture, conn = ledger
    # Capture is at12:00, but this hypothetical decision was at11:59. Data arriving
    # at11:59:30 must not be attached to that earlier decision, even within5 minutes.
    changes = {'analysis': analysis(as_of='2026-10-15T11:59:00Z')}
    if late_fact in {'publication', 'retrieval'}:
        db.archive_source(conn, 'late', 'https://example.org/late', 'company_ir',
            '2026-10-15T11:59:30Z' if late_fact == 'publication' else '2026-10-15T10:00:00Z',
            '2026-10-15T11:59:30Z', 'Evidence arriving after the decision')
        changes['source_ids'] = ['late']
    elif late_fact == 'forecast':
        changes['forecast'] = {'origin': 'issuer_announced', 'window': None,
                                'available_at': '2026-10-15T11:59:30Z'}
    else:
        with conn:
            conn.execute("UPDATE model_validation SET updated_at='2026-10-15T11:59:30Z'")
    before = list(conn.iterdump())
    with pytest.raises(ValueError):
        observe(capture, conn, **changes)
    assert list(conn.iterdump()) == before


def test_capture_time_is_after_materializing_the_observation_not_before_reads(ledger, monkeypatch):
    capture, conn = ledger
    clock = iter(['2026-10-15T12:00:00Z', '2026-10-15T12:00:05Z'])
    monkeypatch.setattr(capture, 'utcnow', lambda: next(clock))
    row = observe(capture, conn)
    assert capture.as_of(conn, 'EVENT-1', '2026-10-15T12:00:03Z') == []
    assert row['captured_at'].startswith('2026-10-15T12:00:05')
