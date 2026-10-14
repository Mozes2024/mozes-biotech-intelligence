"""Periodic form coverage starts quietly; later publications remain changes."""
from datetime import date

import pytest

from mozes import db
from mozes.financing_intelligence import refresh_financing, financing_context
from mozes.intelligence_store import state_put, state_get, ensure_schema


@pytest.fixture
def coverage(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / 'coverage.db')
    ensure_schema(conn)
    db.upsert_watch(conn, 'AAA', cik='123')
    state_put(conn, 'financing_baselined:AAA', True)
    filings = []
    clock = ['2026-10-01T12:00:00Z']
    monkeypatch.setattr('mozes.financing_intelligence.utcnow', lambda: clock[0])
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', lambda *a, **kw: list(filings))
    monkeypatch.setattr('mozes.ingest.edgar.filing_documents', lambda f: [{'url': f['url']}])
    monkeypatch.setattr('mozes.ingest.edgar._get', lambda url:
        'During the three months ended June 30, 2026, we sold shares under our ATM program with net proceeds of $11 million.')
    def filing(form, accession, published):
        return {'form': form, 'accession': accession, 'accepted': published, 'filed': published[:10],
                'url': f'https://www.sec.gov/Archives/edgar/data/123/{accession}/filing.htm'}
    return conn, filings, clock, filing


def test_already_baselined_issuer_periodic_history_enriches_without_new_changes(coverage):
    conn, filings, clock, filing = coverage
    filings.append(filing('10-Q', 'historical', '2026-09-15T10:00:00Z'))
    result = refresh_financing(conn, today=date(2026, 10, 1))
    assert result['identified'] == 1
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0
    context = financing_context(conn, 'AAA', as_of=date(2026, 10, 2))
    assert context[0]['kind'] == 'atm_sales' and context[0]['net_proceeds_available']


@pytest.mark.parametrize('form', ['10-Q', '10-K', '20-F'])
def test_later_periodic_publications_emit_once_but_late_discovered_history_stays_quiet(coverage, form):
    conn, filings, clock, filing = coverage
    refresh_financing(conn, today=date(2026, 10, 1))  # Empty initial coverage still establishes boundary.
    clock[0] = '2026-10-02T12:00:00Z'
    filings.extend([filing(form, 'new', '2026-10-02T09:00:00Z'),
                    filing(form, 'old', '2026-09-16T09:00:00Z')])
    refresh_financing(conn, today=date(2026, 10, 2))
    rows = conn.execute('SELECT source_url FROM change_events').fetchall()
    assert len(rows) == 1 and '/new/' in rows[0]['source_url']
    immutable = list(conn.execute('SELECT * FROM v2c_financing_events'))
    refresh_financing(conn, today=date(2026, 10, 2))
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 1
    assert list(conn.execute('SELECT * FROM v2c_financing_events')) == immutable


@pytest.mark.parametrize('form', ['8-K', '6-K'])
def test_existing_form_signals_remain_enabled_during_initial_periodic_coverage(coverage, form):
    conn, filings, clock, filing = coverage
    filings.append(filing(form, 'existing-form', '2026-09-30T09:00:00Z'))
    refresh_financing(conn, today=date(2026, 10, 1))
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 1


def test_coverage_boundary_survives_failed_initial_source_read(coverage, monkeypatch):
    conn, filings, clock, filing = coverage
    def failed(*a, **kw): raise RuntimeError('offline source failure')
    with monkeypatch.context() as patch:
        patch.setattr('mozes.ingest.edgar.recent_filings_v2', failed)
        assert refresh_financing(conn, today=date(2026, 10, 1))['error_count'] == 1
    assert state_get(conn, 'financing_periodic_coverage_started:AAA') == '2026-10-01T12:00:00Z'
    clock[0] = '2026-10-02T12:00:00Z'
    filings.append(filing('10-Q', 'old', '2026-09-15T10:00:00Z'))
    refresh_financing(conn, today=date(2026, 10, 2))
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0


def test_publication_during_initial_source_read_is_a_valid_new_signal(coverage, monkeypatch):
    conn, filings, clock, filing = coverage
    def sources(*a, **kw):
        clock[0] = '2026-10-01T12:02:00Z'
        return [filing('10-Q', 'just-published', '2026-10-01T12:01:00Z')]
    monkeypatch.setattr('mozes.ingest.edgar.recent_filings_v2', sources)
    refresh_financing(conn, today=date(2026, 10, 1))
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 1
    assert state_get(conn, 'financing_periodic_coverage_started:AAA') == '2026-10-01T12:00:00Z'


@pytest.mark.parametrize('published', ['2026-10-01T12:00:00Z', '2026-10-01'])
def test_boundary_and_date_only_publications_are_conservatively_initial_coverage(coverage, published):
    conn, filings, clock, filing = coverage
    filings.append(filing('10-Q', 'boundary', published))
    refresh_financing(conn, today=date(2026, 10, 1))
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
