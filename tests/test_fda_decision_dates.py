import json
from datetime import datetime, timezone
from xml.sax.saxutils import escape

import pytest
from mozes import db
from mozes.fda_dates import decision_date, validate_item
from mozes.primary_feeds import FDA_FEEDS, poll_fda_feeds

NOW = datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc)
BASE = 'http://www.fda.gov/drugs/cancer-approvals/'
CASES = [
 ('fda-grants-accelerated-approval-epcoritamab-bysp-relapsed-or-refractory-follicular-lymphoma',
  'FDA grants accelerated approval to epcoritamab-bysp for relapsed or refractory follicular lymphoma',
  'On June 26, 2024, the Food and Drug Administration granted accelerated approval to epcoritamab-bysp (Epkinly, Genmab US, Inc.).', '2024-06-26'),
 ('fda-approves-tisotumab-vedotin-tftv-recurrent-or-metastatic-cervical-cancer',
  'FDA approves tisotumab vedotin-tftv for recurrent or metastatic cervical cancer',
  'On April 29, 2024, the Food and Drug Administration granted traditional approval to tisotumab vedotin-tftv (Tivdak).', '2024-04-29'),
 ('fda-grants-accelerated-approval-lifileucel-unresectable-or-metastatic-melanoma',
  'FDA grants accelerated approval to lifileucel for unresectable or metastatic melanoma',
  'On February 16, 2024, the Food and Drug Administration granted accelerated approval to lifileucel (Amtagvi, Iovance Biotherapeutics, Inc.).', '2024-02-16'),
]


def item(case, summary=None):
    slug, title, body, day = case
    return dict(url=BASE + slug, title=title, summary=body if summary is None else summary,
                published_at='2026-11-09T21:50:41+00:00')


def rss(items):
    return '<rss><channel>' + ''.join('<item><title>' + escape(i['title']) + '</title><link>' + escape(i['url']) + '</link><description>' + escape(i['summary']) + '</description><pubDate>Mon, 09 Nov 2026 16:50:41 EST</pubDate></item>' for i in items) + '</channel></rss>'


@pytest.mark.parametrize('case', CASES)
def test_source_dates(case):
    assert decision_date(case[2]) == case[3]


def test_old_approvals_never_enqueue_even_with_fresh_rss(tmp_path):
    conn = db.connect(tmp_path / 'fda.db')
    items = [item(CASES[0]), item(CASES[1]), item(CASES[2], summary=CASES[2][1])]
    calls = []
    def article(url):
        calls.append(url)
        return '<main><p>' + CASES[2][2] + '</p></main>'
    raw = rss(items)
    result = poll_fda_feeds(conn, fetch=lambda _: raw, now=NOW,
                           resolve_headline=lambda *_: {'ticker': 'GMAB'}, read_article=article)
    assert result['historical_decisions'] == 9  # three overlapping FDA feeds
    assert result['unverified_decision_dates'] == 0
    assert len(calls) == 1  # only headline-only summary needs a body read, cached across feeds
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM alert_outbox').fetchone()[0] == 0
    observations = [json.loads(r[0]) for r in conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key LIKE 'fda_decision_date:%'")]
    assert {x['decision_date'] for x in observations} == {x[3] for x in CASES}
    assert all(x['status'] == 'historical_decision' for x in observations)
    assert all(x['feed_published_at'].startswith('2026-11-09') for x in observations)


def test_real_new_approval_retains_rss_time_and_provenance(tmp_path):
    conn = db.connect(tmp_path / 'new.db')
    i = item(CASES[0]); i['summary'] = i['summary'].replace('June 26, 2024', 'November 9, 2026')
    result = poll_fda_feeds(conn, fetch=lambda _: rss([i]), now=NOW,
                           resolve_headline=lambda *_: {'ticker': 'GMAB'},
                           read_article=lambda _: pytest.fail('summary date suffices'))
    assert result['historical_decisions'] == result['unverified_decision_dates'] == 0
    row = conn.execute('SELECT new_value,metadata_json FROM change_events LIMIT 1').fetchone()
    assert json.loads(row[0])['published_at'] == '2026-11-09T21:50:41+00:00'
    meta = json.loads(row[1])
    assert meta['decision_date'] == '2026-11-09'
    assert meta['date_basis'] == 'feed_summary'
    assert meta['feed_published_at'] == '2026-11-09T21:50:41+00:00'


def test_missing_body_date_is_visible_partial_not_fresh(tmp_path):
    conn = db.connect(tmp_path / 'missing.db')
    i = item(CASES[2], summary=CASES[2][1])
    result = poll_fda_feeds(conn, fetch=lambda _: rss([i]), now=NOW,
        resolve_headline=lambda *_: {'ticker': 'IOVA'}, read_article=lambda _: '<p>No dated decision</p>')
    assert result['unverified_decision_dates'] == 3
    assert conn.execute('SELECT COUNT(*) FROM change_events').fetchone()[0] == 0
    states = [json.loads(r[0]) for r in conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key LIKE 'fda_feed:%'")]
    assert all(x['status'] == 'PARTIAL' and x['error'] == 'decision_date_unverified' for x in states)


def test_body_read_errors_do_not_abort_other_items(tmp_path):
    conn = db.connect(tmp_path / 'error.db')
    i = item(CASES[2], summary=CASES[2][1])
    def fail(_): raise OSError('unavailable')
    out, state = validate_item(conn, i, now=NOW, read_article=fail, budget=[1])
    assert out is None and state == 'date_unverified'
    state = json.loads(conn.execute('SELECT value_json FROM monitor_observations').fetchone()[0])
    assert 'OSError' in state['error']


def test_bounded_body_reads_and_no_future_date(tmp_path):
    conn = db.connect(tmp_path / 'budget.db')
    i = item(CASES[2], summary=CASES[2][1])
    assert validate_item(conn, i, now=NOW, read_article=lambda _: pytest.fail('no budget'), budget=[0])[0] is None
    i['summary'] = CASES[2][2].replace('February 16, 2024', 'November 10, 2026')
    assert validate_item(conn, i, now=NOW, read_article=lambda _: '', budget=[0])[1] == 'date_unverified'


def test_only_opening_decision_not_random_trial_date():
    assert decision_date('Trial started June 26, 2024. FDA approved today.') is None
    assert decision_date('On November 9, 2026, patients completed a study. FDA approval pending.') is None
    assert decision_date('On February 30, 2024, the FDA approved a drug.') is None


def test_other_fda_pages_unchanged(tmp_path):
    conn = db.connect(tmp_path / 'other.db')
    i = item(CASES[0]); i['url'] = 'https://www.fda.gov/news-events/press-announcements/new'
    assert validate_item(conn, i, now=NOW, read_article=lambda _: pytest.fail('not applicable'), budget=[0]) == (i, 'not_applicable')


def test_two_day_old_decision_cannot_use_todays_rss(tmp_path):
    conn = db.connect(tmp_path / 'cutoff.db')
    i = item(CASES[0]); i['summary'] = i['summary'].replace('June 26, 2024', 'November 7, 2026')
    assert validate_item(conn, i, now=NOW, read_article=lambda _: '', budget=[0])[1] == 'historical_decision'


def test_body_cache_revalidates_on_new_feed_timestamp(tmp_path):
    conn = db.connect(tmp_path / 'cache.db')
    i = item(CASES[2], summary=CASES[2][1])
    calls = []
    def read(_): calls.append(1); return CASES[2][2]
    for _ in range(2): validate_item(conn, i, now=NOW, read_article=read, budget=[1])
    assert len(calls) == 1
    i['published_at'] = '2026-11-09T21:51:00+00:00'
    validate_item(conn, i, now=NOW, read_article=read, budget=[1])
    assert len(calls) == 2
