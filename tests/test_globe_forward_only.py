import json
from datetime import datetime,timedelta,timezone
from email.utils import format_datetime
from mozes import db,primary_feeds as feeds


def test_official_rss_host_transition_never_replays_old_rapp_and_deduplicates_future_items(tmp_path,monkeypatch):
    conn=db.connect(tmp_path/'feed.db')
    conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)',('rapport','Rapport Therapeutics, Inc.','RAPP','2012593',.9,'SEC-v2C-equity','2026-10-06T06:32:14Z'))
    selected=tuple((name,url) for name,url in feeds.WIRE_FEEDS if name.startswith('globenewswire'))
    assert len(selected)==2 and all('https://rss.globenewswire.com/' in url for _,url in selected)
    monkeypatch.setattr(feeds,'WIRE_FEEDS',selected)
    now=datetime(2026,10,20,13,20,tzinfo=timezone.utc)
    def rss(at,slug):
        return f'<rss><channel><item><title>Rapport Therapeutics Phase 2 trial met its primary endpoint</title><link>https://www.globenewswire.com/news-release/{slug}</link><pubDate>{format_datetime(at)}</pubDate></item></channel></rss>'.encode()
    old=rss(now-timedelta(hours=3),'3384169/rapport-results.html')
    assert feeds.poll_wire_feeds(conn,fetch=lambda _:old,now=now)['signals_seen']==0
    assert conn.execute('SELECT count(*) FROM change_events').fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM alert_outbox').fetchone()[0]==0
    # A synthetic future release tests ongoing collection, not a second historical RAPP event.
    future=now+timedelta(minutes=2)
    new=rss(future,'synthetic-future-release.html')
    feeds.poll_wire_feeds(conn,fetch=lambda _:new,now=future)
    feeds.poll_wire_feeds(conn,fetch=lambda _:new,now=future+timedelta(minutes=1))
    assert conn.execute('SELECT count(*) FROM change_events').fetchone()[0]==1
    assert conn.execute('SELECT count(*) FROM alert_outbox').fetchone()[0]==1
    assert conn.execute('SELECT count(*) FROM alert_deliveries').fetchone()[0]==0
    values=[json.loads(r[0]) for r in conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key LIKE 'wire_transport_baseline:%'")]
    assert len(values)==2 and all(v['activated_at']==now.isoformat() for v in values)


def test_failed_fetch_cannot_start_the_forward_only_watermark(tmp_path,monkeypatch):
    conn=db.connect(tmp_path/'failed.db')
    monkeypatch.setattr(feeds,'WIRE_FEEDS',tuple(r for r in feeds.WIRE_FEEDS if r[0].startswith('globenewswire')))
    def failed(_):raise OSError('HTTP 403')
    assert feeds.poll_wire_feeds(conn,fetch=failed)['errors']==2
    assert conn.execute("SELECT count(*) FROM monitor_observations WHERE observation_key LIKE 'wire_transport_baseline:%'").fetchone()[0]==0


def test_untrusted_article_cannot_establish_a_successful_activation(tmp_path,monkeypatch):
    conn=db.connect(tmp_path/'untrusted.db')
    monkeypatch.setattr(feeds,'WIRE_FEEDS',tuple(r for r in feeds.WIRE_FEEDS if r[0].startswith('globenewswire')))
    now=datetime(2026,10,20,13,20,tzinfo=timezone.utc)
    raw=f'<rss><channel><item><title>Phase 2 positive results</title><link>https://attacker.example/results</link><pubDate>{format_datetime(now)}</pubDate></item></channel></rss>'.encode()
    assert feeds.poll_wire_feeds(conn,fetch=lambda _:raw,now=now)['errors']==2
    assert conn.execute("SELECT count(*) FROM monitor_observations WHERE observation_key LIKE 'wire_transport_baseline:%'").fetchone()[0]==0
