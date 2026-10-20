from mozes import db
from mozes.priority import priority_tickers
from mozes.refresh import _select_issuer_batch


def test_forty_hot_names_cannot_consume_every_discovery_slot(tmp_path):
    conn = db.connect(tmp_path / 'fair.db')
    hot = priority_tickers()
    rows = [dict(ticker=t, last_attempt=None, first_seen='2026-10-01', pending_material_at=None)
            for t in (*hot, 'TYRA', 'ZZZZ', 'CAND3', 'CAND4', 'CAND5', 'CAND6')]
    chosen = _select_issuer_batch(conn, rows, limit=25)
    assert len(chosen) == len(set(chosen)) == 25
    assert len(set(chosen) - set(hot)) == 5
    for row in rows:
        if row['ticker'] in chosen:
            row['last_attempt'] = '2026-10-20T12:00:00Z'
    assert 'ZZZZ' in _select_issuer_batch(conn, rows, limit=25)
    assert priority_tickers() == hot


def test_verified_pending_material_work_precedes_hot_queue_without_increasing_budget(tmp_path):
    conn = db.connect(tmp_path / 'material.db')
    rows = [dict(ticker=t,last_attempt=None,first_seen='2026-10-01',pending_material_at=None)
            for t in priority_tickers()]
    rows.append(dict(ticker='TYRA',last_attempt=None,first_seen='2026-10-20',pending_material_at='2026-10-20'))
    chosen = _select_issuer_batch(conn, rows, limit=25)
    assert 'TYRA' in chosen
    assert len(chosen) == 25
