"""TYRA identity must not depend on the hot cohort or create historical sends."""
import json
from pathlib import Path
import pytest

from mozes import db
from mozes.alert_dispatch import STAGE2, enqueue_change, sync_new_changes
from mozes.ingest.edgar_latest import biotech_universe
from mozes.ir_registry import load_ir_registry, resolve_alias
from mozes.live_monitor import recent_changes
from mozes.priority import priority_tickers


def test_tyra_official_identity_and_context_bound_aliases():
    entry = load_ir_registry()['TYRA']
    assert entry['site'] == 'https://ir.tyra.bio/'
    assert entry['feed_url'] is None
    assert resolve_alias('Tyra Biosciences reports SURF302 results')['ticker'] == 'TYRA'
    assert resolve_alias('dabogratinib results') is None
    assert resolve_alias('SURF302 trial') is None


def test_historical_tyra_records_are_visible_changes_and_never_enqueue(tmp_path):
    from mozes.tyra_history import import_history
    conn = db.connect(tmp_path / 'history.db')
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ('tyra', 'Tyra Biosciences, Inc.', 'TYRA', '1863127', .9, 'SEC-v2C-equity', db.utcnow()))
    conn.execute("INSERT INTO discovery_candidates(candidate_id,nct_id,ticker,phase,status,raw_json,discovered_at) VALUES(?,?,?,?,?,?,?)",
                 ('TYRA-test', 'NCT05544552', 'TYRA', 'PHASE1/PHASE2', 'ACTIVE_NOT_RECRUITING', '{}', db.utcnow()))
    conn.commit()
    assert biotech_universe(conn)['1863127']['ticker'] == 'TYRA'
    ids = import_history(conn)
    assert import_history(conn) == ids
    assert len(ids) == 2
    rows = conn.execute("SELECT * FROM change_events WHERE ticker='TYRA'").fetchall()
    assert len(rows) == 2
    values = [json.loads(row['new_value']) for row in rows]
    financing = next(v for v in values if v.get('financing_status'))
    assert financing['financing_status'] == 'priced'
    assert financing['expected_gross_proceeds_usd'] == 400_000_000
    assert 'net_proceeds_usd' not in financing
    clinical = next(v for v in values if not v.get('financing_status'))
    assert clinical['event_status'] == 'results_reported'
    for change_id in ids:
        assert enqueue_change(conn, change_id, channels=['log']) == []
        assert enqueue_change(conn, change_id, stage=STAGE2, channels=['log']) == []
    sync_new_changes(conn)
    assert conn.execute('SELECT count(*) FROM alert_outbox').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM alert_deliveries').fetchone()[0] == 0
    assert {row['change_id'] for row in recent_changes(conn)} == set(ids)
    assert all(row['metadata']['historical_only'] for row in recent_changes(conn))
    assert {row[0] for row in conn.execute('SELECT decision FROM alert_enqueue_decisions')} == {'historical_only'}


@pytest.mark.parametrize('ticker,cik', [('TYRA', '1863127'), ('ZZZZ', '1234567')])
@pytest.mark.parametrize('text,polarity', [
    ('Company met its primary endpoint with statistically significant Phase 2 results', 'positive'),
    ('Company did not meet its primary endpoint in Phase 2 trial', 'negative'),
])
def test_nonpriority_phase2_source_to_stage1(tmp_path, monkeypatch, ticker, cik, text, polarity):
    from mozes.ingest import edgar_latest
    from mozes.alert_dispatch import recent_alerts
    assert ticker not in priority_tickers()
    assert len(priority_tickers()) == 40
    conn = db.connect(tmp_path / 'ingestion.db')
    conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',
                 (ticker.lower(), ticker + ' Biosciences', ticker, cik, .99, 'SEC-v2C-equity', db.utcnow()))
    conn.execute('INSERT INTO discovery_candidates(candidate_id,ticker,phase,status,raw_json,discovered_at) VALUES(?,?,?,?,?,?)',
                 (ticker, ticker, 'PHASE2', 'RECRUITING', '{}', db.utcnow()))
    conn.commit()
    fixture = (Path(__file__).parent / 'fixtures/sec_latest_8k.atom').read_text()
    fixture = fixture.replace('(0001035422)', '(' + cik + ')')
    monkeypatch.setattr(edgar_latest, 'filing_documents', lambda filing: [
        {'kind': 'ex99', 'url': filing['url']}])
    def document(url):
        return json.dumps({'directory': {'item': [{'name': 'ex99.htm'}]}}) if url.endswith('index.json') else text
    result = edgar_latest.poll_latest(conn, forms=('8-K',), fetch=lambda _: fixture, document_fetch=document)
    assert result['material'] == 1
    changes = recent_changes(conn)
    assert changes[0]['ticker'] == ticker
    assert changes[0]['new_value']['outcome']['polarity'] == polarity
    assert recent_alerts(conn)[0]['ticker'] == ticker
    assert edgar_latest.poll_latest(conn, forms=('8-K',), fetch=lambda _: fixture, document_fetch=document)['seen'] == 0
