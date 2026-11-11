"""An SEC name prefix is not proof that a different issuer owns a release."""
from datetime import datetime, timezone
import json

import pytest
from mozes import db
from mozes.clinical_events import ingest_clinical, resolve_issuer
from mozes.news_signals import resolve_headline
from mozes.primary_feeds import WIRE_FEEDS, poll_wire_feeds
from mozes.universe import normalize_org, conflicting_issuer_prefix

HEADLINE = ('Beeline Medicines Announces Presentation of Phase 2 Results of Afimetoran '
            'in Systemic Lupus Erythematosus at ACR Convergence 2026')
SUMMARY = 'All three afimetoran doses met the primary endpoint with a placebo-adjusted difference of more than 30 percentage points.'
URL = 'https://www.globenewswire.com/news-release/2026/11/11/3400746/0/en/beeline-medicines-results.html'
NOW = datetime(2026, 11, 11, 14, 20, tzinfo=timezone.utc)


def mapped(conn):
    conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',
                 ('beeline', 'Beeline Holdings, Inc.', 'BLNE', '1534708', .9,
                  'SEC-v2C-equity', '2026-10-06'))
    conn.commit()


@pytest.mark.parametrize('summary', [SUMMARY, SUMMARY + ' (NASDAQ: BLNE)'])
def test_medicines_is_not_holdings_even_with_ticker_hint(tmp_path, summary):
    conn = db.connect(tmp_path / 'issuer.db'); mapped(conn)
    assert resolve_issuer(conn, HEADLINE, summary) is None
    assert resolve_headline(conn, HEADLINE) is None
    result = ingest_clinical(conn, headline=HEADLINE, summary=summary, source_url=URL,
                             published_at='2026-11-11T14:00:00Z', source_type='wire')
    assert result['change_id'] is None
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM watch_universe').fetchone()[0] == 0
    assert tuple(conn.execute('SELECT ticker,lifecycle,suppression_reason FROM clinical_events').fetchone()) == (None, 'SUPPRESSED', 'issuer_unresolved')


@pytest.mark.parametrize('name', ['Beeline Holdings', 'Beeline Holdings, Inc.'])
def test_full_holdings_name_remains_resolvable(tmp_path, name):
    conn = db.connect(tmp_path / 'issuer.db'); mapped(conn)
    headline = name + ' Announces Quarterly Results'
    assert resolve_issuer(conn, headline)['ticker'] == 'BLNE'
    assert resolve_headline(conn, headline)['ticker'] == 'BLNE'
    # Cached discovery names use the same evidence normalization.
    rows = [dict(r) for r in conn.execute('SELECT * FROM sponsor_ticker_map')]
    rows[0]['_headline_name'] = normalize_org(rows[0]['sponsor'])
    assert resolve_headline(conn, headline, rows)['ticker'] == 'BLNE'
    assert resolve_headline(conn, HEADLINE, rows) is None


def test_sponsor_mapping_normalization_is_unchanged():
    assert normalize_org('Beeline Holdings, Inc.') == 'beeline'
    assert conflicting_issuer_prefix('Beeline Holdings, Inc.', HEADLINE)
    assert not conflicting_issuer_prefix('Harmony Biosciences Holdings', 'Harmony Biosciences Reports Trial Results')
    assert not conflicting_issuer_prefix('Beeline Holdings, Inc.', 'Beeline Announces Quarterly Results')


def test_wire_keeps_clinical_observation_without_wrong_ticker(tmp_path):
    conn = db.connect(tmp_path / 'wire.db'); mapped(conn)
    def fetch(url):
        if url != WIRE_FEEDS[0][1]:
            return b'<rss><channel/></rss>'
        return (f'<rss><channel><item><title>{HEADLINE}</title><link>{URL}</link>'
                f'<description>{SUMMARY}</description><pubDate>Wed, 11 Nov 2026 14:00:00 GMT</pubDate>'
                '</item></channel></rss>').encode()
    poll_wire_feeds(conn, fetch=fetch, now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM change_events WHERE ticker='BLNE'").fetchone()[0] == 0
    assert all(json.loads(r[0]).get('ticker') != 'BLNE' for r in conn.execute('SELECT payload_json FROM alert_outbox'))
    changes = conn.execute('SELECT ticker,severity FROM change_events').fetchall()
    assert [tuple(r) for r in changes] == [(None, 'medium')]


def test_secondary_news_query_cannot_reintroduce_holdings_collision(tmp_path, monkeypatch):
    from mozes import news_signals
    conn = db.connect(tmp_path / 'secondary.db'); mapped(conn)
    db.upsert_watch(conn, 'BLNE', cik='1534708', company='Beeline Holdings, Inc.', source='dynamic_news_discovery')
    monkeypatch.setattr(news_signals, 'discover_news_issuers', lambda *a, **k: ({'signals_seen': 0}, []))
    monkeypatch.setattr(news_signals, 'poll_official_feeds', lambda *a, **k: {'signals_seen': 0})
    monkeypatch.setattr(news_signals, 'parse_feed', lambda *a, **k: [{
        'title': HEADLINE, 'summary': SUMMARY, 'url': URL,
        'publisher_url': 'https://www.globenewswire.com', 'published_at': NOW.isoformat()}])
    news_signals.poll_news(conn, fetch=lambda _: b'', now=NOW)
    assert conn.execute("SELECT COUNT(*) FROM change_events WHERE ticker='BLNE'").fetchone()[0] == 0


@pytest.mark.parametrize('ticker,sponsor,headline', [
    ('HRMY', 'Harmony Biosciences Holdings, Inc.', 'Harmony Biosciences Reports Phase 3 Results'),
    ('IMCR', 'Immunocore Holdings plc', 'Immunocore Presents Clinical Data'),
    ('WGS', 'GeneDx Holdings Corp.', 'GeneDx Research Demonstrates Medical Actionability'),
    ('SRRK', 'Scholar Rock Holding Corp', 'Scholar Rock Reports Phase 3 Results'),
    ('BLNE', 'Beeline Holdings, Inc.', 'Beeline Announces Quarterly Results'),
])
def test_abbreviated_legitimate_names_remain_resolvable(tmp_path, ticker, sponsor, headline):
    conn = db.connect(tmp_path / 'abbreviated.db')
    conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',
                 (normalize_org(sponsor), sponsor, ticker, '123', .9, 'SEC-v2C-equity', '2026-11-11'))
    conn.commit()
    assert resolve_issuer(conn, headline)['ticker'] == ticker
    assert resolve_headline(conn, headline)['ticker'] == ticker
