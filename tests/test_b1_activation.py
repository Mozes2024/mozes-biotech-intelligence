"""Offline contracts for the existing hot lineage's opt-in financing observation."""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

import pytest

from mozes import db, financing_intelligence as financing
from mozes.ingest.edgar import recent_filings_v2 as real_recent_filings
from mozes.intelligence_store import digest, ensure_schema, state_get

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def observation(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / 'observation.db')
    ensure_schema(conn)
    monkeypatch.setenv('SEC_USER_AGENT', 'Offline Test test@example.com')
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR', 'CYTK', 'KOD', 'PHAR', 'QTTB', 'SMMT'))
    for i, ticker in enumerate(('CAPR', 'CYTK', 'KOD', 'PHAR', 'QTTB', 'SMMT', 'OUTSIDE'), 1):
        db.upsert_watch(conn, ticker, cik=str(i))
    monkeypatch.setattr(financing, 'utcnow', lambda: NOW.isoformat())
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda *a, **kw: [])
    yield conn
    conn.close()


def filing(cik='1', accession='new', published='2026-10-13T13:00:00Z', form='10-Q'):
    return {'form': form, 'accession': accession, 'accepted': published, 'filed': published[:10],
            'url': f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/primary.htm',
            'index_url': f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json'}


def test_six_hour_attempt_cadence_survives_restart_and_failures(observation, monkeypatch):
    conn = observation
    def failed(*a, **kw):
        raise OSError('offline')
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', failed)
    result = financing.refresh_due_financing(conn, now=NOW)
    assert result['status'] == 'PARTIAL' and result['processed'] == 5
    assert state_get(conn, 'b1_financing_observation')['started_at'] == NOW.isoformat()
    second = db.connect(conn.execute('PRAGMA database_list').fetchone()[2])
    assert financing.refresh_due_financing(second, now=NOW + timedelta(hours=6, microseconds=-1))['reason'] == 'not_due'
    assert financing.refresh_due_financing(second, now=NOW + timedelta(hours=6))['processed'] == 5
    second.close()


def test_rotation_is_bounded_to_existing_priority_issuers(observation, monkeypatch):
    conn = observation
    seen = []
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda cik, **kw: seen.append(cik) or [])
    financing.refresh_due_financing(conn, now=NOW)
    assert seen == ['1', '2', '3', '4', '5']
    monkeypatch.setattr(financing, 'utcnow', lambda: (NOW + timedelta(hours=6)).isoformat())
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=6))
    assert seen[5] == '6' and '7' not in seen
    monkeypatch.setattr(financing, 'utcnow', lambda: (NOW + timedelta(hours=12)).isoformat())
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=12))
    assert seen[10] == '5'  # The one older issuer, not the same alphabetic prefix again.


def test_missing_identity_does_not_start_observation_or_reveal_identity(observation, monkeypatch):
    monkeypatch.setenv('SEC_USER_AGENT', '   ')
    result = financing.refresh_due_financing(observation, now=NOW)
    assert result == {'status': 'SKIPPED', 'reason': 'SEC_USER_AGENT missing'}
    assert state_get(observation, 'b1_financing_observation') is None


@pytest.mark.parametrize('form', ['10-Q', '10-K', '20-F', '8-K', '6-K', 'S-3'])
def test_failed_first_scan_keeps_history_quiet_and_emits_later_publication_once(observation, monkeypatch, form):
    conn = observation
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR',))
    def failed(*a, **kw):
        raise OSError('offline')
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', failed)
    financing.refresh_due_financing(conn, now=NOW)
    # No successful legacy baseline: a publication after activation still matters.
    assert not state_get(conn, 'financing_baselined:CAPR', False)
    sources = [filing(form=form), filing(accession='history', published='2026-10-12T13:00:00Z', form=form)]
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda *a, **kw: sources)
    monkeypatch.setattr('mozes.ingest.edgar.filing_documents', lambda f, **kw: ([{'url': f['url']}], []))
    monkeypatch.setattr('mozes.ingest.edgar._get', lambda *a, **kw: 'We completed an offering with net proceeds of $20 million.')
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=6))
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=12))
    changes = conn.execute('SELECT source_url FROM change_events').fetchall()
    assert len(changes) == 1 and '/new/' in changes[0][0]
    assert conn.execute('SELECT COUNT(*) FROM v2c_financing_events').fetchone()[0] == 2
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0


def test_partial_exhibit_index_is_reported_without_losing_primary_evidence(observation, monkeypatch):
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR',))
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda *a, **kw: [filing(form='8-K')])
    monkeypatch.setattr('mozes.ingest.edgar.filing_documents', lambda f, **kw:
                        ([{'url': f['url']}], [{'status': 'INCOMPLETE', 'reason': 'index_unavailable'}]))
    monkeypatch.setattr('mozes.ingest.edgar._get', lambda *a, **kw: 'We completed an offering with net proceeds of $20 million.')
    result = financing.refresh_due_financing(observation, now=NOW)
    assert result['status'] == 'PARTIAL' and result['identified'] == 1
    assert not state_get(observation, 'financing_baselined:CAPR', False)
    assert financing.refresh_due_financing(observation, now=NOW + timedelta(minutes=15))['reason'] == 'not_due'


def test_partial_document_failure_keeps_success_and_retry_does_not_duplicate(observation, monkeypatch):
    conn = observation
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR',))
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda *a, **kw: [filing(form='8-K')])
    monkeypatch.setattr('mozes.ingest.edgar.filing_documents', lambda f, **kw:
                        ([{'url': f['url']}, {'url': f['url'] + '/ex99'}], []))
    unavailable = [True]
    def source(url, **kw):
        if unavailable[0] and url.endswith('/ex99'):
            raise OSError('offline exhibit')
        return 'We completed an offering with net proceeds of $20 million.'
    monkeypatch.setattr('mozes.ingest.edgar._get', source)
    result = financing.refresh_due_financing(conn, now=NOW)
    assert result['status'] == 'PARTIAL' and result['identified'] == 1
    assert not state_get(conn, 'financing_baselined:CAPR', False)
    primary = dict(conn.execute('SELECT * FROM v2c_financing_events').fetchone())
    unavailable[0] = False
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=6))
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=12))
    assert conn.execute('SELECT COUNT(*) FROM v2c_financing_events').fetchone()[0] == 2
    assert dict(conn.execute('SELECT * FROM v2c_financing_events WHERE record_id=?', (primary['record_id'],)).fetchone()) == primary
    assert {row[0] for row in conn.execute('SELECT source_url FROM v2c_financing_events')} == {
        filing()['url'], filing()['url'] + '/ex99'}
    context = financing.financing_context(conn, 'CAPR', as_of=date(2026, 10, 14))
    assert len(context) == 1 and len(context[0]['sources']) == 2
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 1


@pytest.mark.parametrize('texts,expected_changes', [
    (['We completed an offering with net proceeds of $20 million.'] * 2, 1),
    (['We completed an offering with net proceeds of $20 million.',
      'We completed an offering with net proceeds of $30 million.'], 2),
    (['On October 1, 2026, we completed an offering with net proceeds of $20 million.',
      'On October 2, 2026, we completed a separate offering with net proceeds of $20 million.'], 2),
])
def test_same_accession_chg_identity_deduplicates_only_exact_text(observation, monkeypatch, texts, expected_changes):
    conn = observation
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR',))
    financing.refresh_due_financing(conn, now=NOW)  # Establish observation before publication.
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda *a, **kw: [filing(form='8-K')])
    urls = [filing()['url'], filing()['url'] + '/ex99']
    monkeypatch.setattr('mozes.ingest.edgar.filing_documents', lambda f, **kw:
                        ([{'url': url} for url in urls], []))
    by_url = dict(zip(urls, texts))
    monkeypatch.setattr('mozes.ingest.edgar._get', lambda url, **kw: by_url[url])
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=6))
    immutable = [dict(row) for row in conn.execute('SELECT * FROM v2c_financing_events ORDER BY source_url')]
    assert len(immutable) == 2
    assert {row['source_url']: row['source_hash'] for row in immutable} == {
        url: digest(text) for url, text in by_url.items()}
    assert {row['source_url']: json.loads(row['payload'])['source_text_hash'] for row in immutable} == {
        url: digest(text) for url, text in by_url.items()}
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == expected_changes
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=12))
    assert [dict(row) for row in conn.execute('SELECT * FROM v2c_financing_events ORDER BY source_url')] == immutable
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == expected_changes
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0


@pytest.mark.parametrize('scope', ['different_accession', 'different_issuer'])
def test_identical_text_does_not_merge_other_publications_or_issuers(observation, monkeypatch, scope):
    conn = observation
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR', 'CYTK'))
    financing.refresh_due_financing(conn, now=NOW)
    def submissions(cik, **kw):
        if scope == 'different_accession':
            return [filing(accession='first'), filing(accession='second')] if cik == '1' else []
        return [filing(cik=cik, accession='same')]
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', submissions)
    monkeypatch.setattr('mozes.ingest.edgar._get', lambda *a, **kw:
                        'We completed an offering with net proceeds of $20 million.')
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=6))
    financing.refresh_due_financing(conn, now=NOW + timedelta(hours=12))
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 2
    assert conn.execute('SELECT COUNT(*) FROM v2c_financing_events').fetchone()[0] == 2


def test_deadline_stops_inside_exhibits_and_unattempted_issuers_keep_rotation_priority(observation, monkeypatch):
    conn = observation
    clock = [0.0]
    monkeypatch.setattr(financing.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda *a, **kw: [filing(form='8-K')])
    monkeypatch.setattr('mozes.ingest.edgar.filing_documents', lambda f, **kw:
                        ([{'url': f['url'] + str(i)} for i in range(4)], []))
    calls = []
    def source(url, *, deadline):
        calls.append((url, deadline))
        clock[0] = 60
        return 'We completed an offering with net proceeds of $20 million.'
    monkeypatch.setattr('mozes.ingest.edgar._get', source)
    result = financing.refresh_due_financing(conn, now=NOW)
    assert result['budget_exhausted'] and result['processed'] == 1
    assert len(calls) == 1 and calls[0][1] == 60
    assert state_get(conn, 'b1_financing_attempt:CYTK') is None
    assert financing.refresh_due_financing(conn, now=NOW + timedelta(minutes=15))['reason'] == 'not_due'


def test_common_deadline_reaches_real_edgar_interfaces(observation, monkeypatch):
    from mozes.ingest import edgar
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR',))
    monkeypatch.setattr(edgar, 'recent_filings_v2', real_recent_filings)
    calls = []
    def transport(url, *, user_agent, deadline):
        calls.append((url, deadline))
        if '/submissions/' in url:
            return json.dumps({'filings': {'recent': {'form': ['8-K'], 'accessionNumber': ['one'],
                'filingDate': ['2026-10-13'], 'acceptanceDateTime': ['2026-10-13T13:00:00Z'],
                'primaryDocument': ['primary.htm']}}})
        if url.endswith('index.json'):
            return json.dumps({'directory': {'item': [{'name': 'primary.htm'}]}})
        return 'We completed an offering with net proceeds of $20 million.'
    monkeypatch.setattr('mozes.sec_http.get_text', transport)
    result = financing.refresh_due_financing(observation, now=NOW)
    assert result['status'] == 'OK' and result['identified'] == 1
    assert len(calls) == 3 and len({deadline for _, deadline in calls}) == 1


def test_filing_and_document_limits_are_enforced_even_for_oversized_sources(observation, monkeypatch):
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR',))
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda *a, **kw:
                        [filing(accession=str(i), form='8-K') for i in range(20)])
    monkeypatch.setattr('mozes.ingest.edgar.filing_documents', lambda f, **kw:
                        ([{'url': f['url'] + str(i)} for i in range(10)], []))
    monkeypatch.setattr('mozes.ingest.edgar._get', lambda *a, **kw: 'No financing disclosure.')
    result = financing.refresh_due_financing(observation, now=NOW)
    assert result['filings_checked'] == 32 and result['document_limit_reached']
    assert result['status'] == 'INCOMPLETE'


@pytest.mark.parametrize('partial', [False, True])
def test_monitor_exports_same_checkpoint_evidence_and_preserves_pit_cutoff(observation, monkeypatch, tmp_path, partial):
    from mozes import live_monitor, pipeline_v2c
    from mozes.edge_enrichment import checkpoint
    conn = observation
    monkeypatch.setenv('MOZES_B1_FINANCING_OBSERVATION', '1')
    monkeypatch.setenv('MOZES_ALERT_DISPATCH', '0')
    monkeypatch.setenv('MOZES_PRIMARY_FEEDS', '0')
    monkeypatch.setenv('GITHUB_RUN_ID', 'offline')
    monkeypatch.setattr('mozes.priority.priority_tickers', lambda: ('CAPR',))
    pipeline_v2c.reconcile_catalog(conn)
    calls = []
    def submissions(cik, **kw):
        calls.append(kw)
        return [] if 'deadline' not in kw else [filing(published='2026-10-12T13:00:00Z', form='8-K' if partial else '10-Q')]
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', submissions)
    monkeypatch.setattr('mozes.ingest.edgar.filing_documents', lambda f, **kw:
                        ([{'url': f['url']}], [{'status': 'INCOMPLETE', 'reason': 'index_unavailable'}]))
    monkeypatch.setattr('mozes.ingest.edgar._get', lambda *a, **kw: 'We completed an offering with net proceeds of $20 million.')
    monkeypatch.setattr(live_monitor, 'reconcile_states', lambda _: [])
    result = live_monitor.run_monitor(conn, audit=False, ctgov_diff=False, news=False, priority_only=True)
    assert result['financing']['identified'] == 1
    assert result['status'] == ('PARTIAL' if partial else 'OK')
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0
    today_payload = pipeline_v2c.export_payload(conn, tmp_path / 'today.json', today=NOW.date())
    assert not next(r for r in today_payload['live'] if r['ticker'] == 'CAPR')['financing_events']
    payload = pipeline_v2c.export_payload(conn, tmp_path / 'data.json', today=date(2026, 10, 14))
    events = next(r for r in payload['live'] if r['ticker'] == 'CAPR')['financing_events']
    assert events[0]['financing_version'] == 'financing-b1-v1'
    assert payload['intelligence_v2c']['modules']['financing']['details']['identified'] == 1
    assert payload['intelligence_v2c']['modules']['financing']['status'] == result['status']
    conn.commit()
    path = Path(conn.execute('PRAGMA database_list').fetchone()[2])
    cp = checkpoint(path)
    assert cp['db_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as restored:
        assert restored.execute('SELECT COUNT(*) FROM v2c_financing_events').fetchone()[0] == 1
        assert json.loads(restored.execute("SELECT payload FROM v2c_state WHERE key='b1_financing_observation'").fetchone()[0])['started_at']


def test_financing_fact_and_provenance_changes_trigger_publication():
    from mozes.pipeline_v2c import semantic_fingerprint
    payload = {'live': [{'id': 'X', 'financing_events': [{'accession': 'same', 'kind': 'completed_offering',
                        'facts': [{'kind': 'net_proceeds', 'value': 20}], 'sources': [{'source_hash': 'a'}]}]}]}
    before = semantic_fingerprint(payload)
    payload['live'][0]['financing_events'][0]['facts'][0]['value'] = 30
    assert semantic_fingerprint(payload) != before
    before = semantic_fingerprint(payload)
    payload['live'][0]['financing_events'][0]['sources'].append({'source_hash': 'b'})
    assert semantic_fingerprint(payload) != before


def test_activation_uses_existing_owner_and_stays_opt_in():
    workflow = (ROOT / '.github/workflows/lightweight-monitor.yml').read_text()
    assert 'MOZES_B1_FINANCING_OBSERVATION: ${{ vars.MOZES_B1_FINANCING_OBSERVATION }}' in workflow
    assert 'group: mozes-hot-monitor' in workflow
    assert workflow.index('Hot priority observe + alert outbox') < workflow.index('Bind durable checkpoint to monitor database')
    assert workflow.index('name: mozes-live-monitor') < workflow.index('mozes.publication_control dispatch')
    assert 'schedule:' not in (ROOT / '.github/workflows/deep-monitor.yml').read_text()
    assert 'schedule:' not in (ROOT / '.github/workflows/nightly.yml').read_text()
