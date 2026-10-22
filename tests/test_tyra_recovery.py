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
    registry_path = Path(__file__).resolve().parents[1] / 'mozes' / 'data' / 'official_ir_sites.json'
    entries = json.loads(registry_path.read_text(encoding='utf-8'), object_pairs_hook=list)
    assert sum(ticker == 'TYRA' for ticker, _ in entries) == 1
    entry = load_ir_registry()['TYRA']
    assert entry['company'] == 'Tyra Biosciences, Inc.'
    assert entry['site'] == 'https://ir.tyra.bio/'
    assert entry['feed_url'] == 'https://ir.tyra.bio/rss/news-releases.xml'
    assert resolve_alias('Tyra Biosciences reports SURF302 results')['ticker'] == 'TYRA'
    assert resolve_alias('dabogratinib results') is None
    assert resolve_alias('SURF302 trial') is None


def test_tyra_blocked_rss_uses_same_host_html_baseline_without_historical_signals(tmp_path):
    from datetime import datetime, timezone
    from urllib.error import HTTPError
    from mozes.news_signals import poll_official_feeds
    conn = db.connect(tmp_path / 'fallback.db')
    old = b'<a href="/news-releases/tyra-september-2026-phase-two-results">Tyra Reports September Phase Two Results</a>'
    page = [old]

    def fetch(url):
        if url.endswith('.xml'):
            raise HTTPError(url, 403, 'Forbidden', {}, None)
        assert url == 'https://ir.tyra.bio/'
        return page[0] + b'<a href="https://fake.example/news-releases/fake-phase-two-results">Fake Phase Two Results Reported Today</a>'

    now = datetime(2026, 10, 20, tzinfo=timezone.utc)
    result = poll_official_feeds(conn, [{'ticker':'TYRA'}], fetch=fetch, now=now)
    assert result['checked'] == 1
    assert conn.execute('SELECT count(*) FROM change_events').fetchone()[0] == 0
    obs = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:TYRA'").fetchone()[0])
    assert obs['status'] == 'html_fallback'
    assert '403' in obs['feed_error']
    poll_official_feeds(conn, [{'ticker':'TYRA'}], fetch=fetch, now=now)
    assert conn.execute('SELECT count(*) FROM alert_outbox').fetchone()[0] == 0


def test_tyra_blocked_rss_and_html_remain_failed_not_operational(tmp_path):
    from datetime import datetime, timezone
    from urllib.error import HTTPError
    from mozes.news_signals import poll_official_feeds
    conn = db.connect(tmp_path / 'blocked.db')

    def blocked(url):
        raise HTTPError(url, 403, 'Forbidden', {}, None)

    result = poll_official_feeds(conn, [{'ticker':'TYRA'}], fetch=blocked, now=datetime(2026,10,20,tzinfo=timezone.utc))
    obs = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='official_feed:TYRA'").fetchone()[0])
    assert obs['status'] == 'fetch_error'
    assert obs['consecutive_failures'] == 1
    assert '403' in obs['error'] and '403' in obs['feed_error']
    assert obs['last_success_at'] is None
    assert result['checked'] == 0 and len(result['errors']) == 1
    assert conn.execute('SELECT count(*) FROM alert_outbox').fetchone()[0] == 0


def test_tyra_is_polled_in_normal_nonpriority_ir_rotation(tmp_path, monkeypatch):
    from mozes import hot_monitor
    conn = db.connect(tmp_path / 'rotation.db')
    selected = []
    for name in ('poll_fda_feeds', 'poll_wire_feeds', 'poll_nasdaq_halts'):
        monkeypatch.setattr(hot_monitor, name, lambda *a, **k: {})
    def poll(conn, issuers, **kwargs):
        selected.extend(row['ticker'] for row in issuers)
        return {}
    monkeypatch.setattr(hot_monitor, 'poll_official_feeds', poll)
    hot_monitor.run_hot_pass(conn, include_sec=False, dispatch=False, fetch=lambda url: b'')
    assert len(priority_tickers()) == 39 and 'TYRA' not in priority_tickers()  # BPMC acquired by Sanofi 2025-07; BGNE renamed ONC
    assert set(selected) == set(load_ir_registry())
    assert selected.count('TYRA') == 1


@pytest.mark.parametrize('link,stamp,operational', [
    ('https://ir.tyra.bio/news-releases/official-release', 'Mon, 19 Oct 2026 12:00:00 GMT', True),
    ('https://fake.example/release', 'Mon, 19 Oct 2026 12:00:00 GMT', False),
    ('https://ir.tyra.bio/news-releases/official-release', 'not a date', False),
])
def test_tyra_readonly_probe_checks_article_provenance_and_dates(link, stamp, operational):
    from datetime import datetime, timezone
    from scripts.probe_tyra_ir import inspect
    class Response:
        status = 200
        def __init__(self, request): self.url = request.full_url
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return self.url
        def read(self, size):
            return f'<rss><channel><item><title>Tyra Reports Results</title><link>{link}</link><pubDate>{stamp}</pubDate></item></channel></rss>'.encode()
    result = inspect(opener=lambda request, **kwargs: Response(request), now=datetime(2026,10,20,tzinfo=timezone.utc))
    assert result['read_only'] is True
    assert result['sources'][0]['operational'] is operational


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
    assert {row[0] for row in conn.execute('SELECT decision FROM alert_enqueue_decisions')} == {'filtered'}


@pytest.mark.parametrize('ticker,cik', [('TYRA', '1863127'), ('ZZZZ', '1234567')])
@pytest.mark.parametrize('text,polarity', [
    ('Company met its primary endpoint with statistically significant Phase 2 results', 'positive'),
    ('Company did not meet its primary endpoint in Phase 2 trial', 'negative'),
])
def test_nonpriority_phase2_source_to_stage1(tmp_path, monkeypatch, ticker, cik, text, polarity):
    from mozes.ingest import edgar_latest
    from mozes.alert_dispatch import recent_alerts
    assert ticker not in priority_tickers()
    assert len(priority_tickers()) == 39  # BPMC acquired by Sanofi 2025-07; BGNE renamed ONC
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
