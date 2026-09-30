from datetime import date, timedelta
from mozes import db
from mozes.engine_v2 import market_setup_from_db


def test_market_setup_reads_database_and_benchmark_by_date(tmp_path):
    conn=db.connect(tmp_path/'m.db')
    d=date(2026,1,1)
    stock=[]; bench=[]
    for i in range(45):
        day=(d+timedelta(days=i)).isoformat()
        stock.append({'date':day,'close':100+i,'volume':1000})
        if i != 10:
            bench.append({'date':day,'close':200+i,'volume':2000})
    db.store_prices(conn,'TEST',stock,'test')
    db.store_prices(conn,'XBI',bench,'test')
    m=market_setup_from_db(conn,'TEST','2026-03-01')
    assert m['available'] is True
    assert 'T-30' in m['returns']
    assert m['relative_to_xbi']
