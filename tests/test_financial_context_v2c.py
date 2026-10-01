from datetime import date
import copy
from mozes import db
from mozes.financial_context import enrich_financial_context, funding_window
from mozes.financial_intelligence import store_financials
from mozes.financing_intelligence import store_financing


def raw():
    common={'end':'2026-06-30','filed':'2026-08-01','form':'10-Q','accn':'0000000123-26-000001'}
    return {'cik':123,'facts':{'us-gaap':{
        'CashAndCashEquivalentsAtCarryingValue':{'units':{'USD':[dict(common,val=30e6)]}},
        'NetCashProvidedByUsedInOperatingActivities':{'units':{'USD':[dict(common,val=-30e6,start='2026-04-01')]}},
    }}}


def test_enrichment_does_not_change_recommendation_or_validation(tmp_path):
    conn=db.connect(tmp_path/'x.db')
    store_financials(conn,'AAA',raw(),as_of='2026-08-02',expected_cik=123)
    rows=[{'id':'AAA-X','ticker':'AAA','recommendation':{'status':'RESEARCH_WORTHY'},'evidence':{'score':60},
           'date':{'window':{'start':'2026-12-01','end':'2026-12-31'}}}]
    before=copy.deepcopy(rows[0])
    enrich_financial_context(conn,rows,date(2026,8,2))
    assert rows[0]['recommendation']==before['recommendation']
    assert rows[0]['evidence']==before['evidence']
    assert rows[0]['funding_window']['status']=='estimate_before_window'
    assert conn.execute('SELECT COUNT(*) FROM model_validation').fetchone()[0]==0


def test_completed_later_financing_is_not_added_to_cash_twice(tmp_path):
    conn=db.connect(tmp_path/'x.db')
    store_financials(conn,'AAA',raw(),as_of='2026-08-02',expected_cik=123)
    filing={'form':'8-K','filed':'2026-08-05','accession':'0000000123-26-000002',
            'url':'https://www.sec.gov/Archives/edgar/data/123/a/ex99.htm'}
    store_financing(conn,'AAA',filing,'We completed the offering with gross proceeds of $50 million.')
    rows=[{'id':'AAA-X','ticker':'AAA','date':{'window':{'start':'2026-12-01','end':'2026-12-31'}}}]
    enrich_financial_context(conn,rows,date(2026,8,6))
    assert rows[0]['financial_context']['cash_usd']==30e6
    assert rows[0]['funding_window']['status']=='later_financing_requires_reconciliation'


def test_missing_financials_are_unknown_not_a_negative_or_zero(tmp_path):
    conn=db.connect(tmp_path/'x.db')
    rows=[{'id':'AAA-X','ticker':'AAA','recommendation':{'status':'RESEARCH_WORTHY'}}]
    enrich_financial_context(conn,rows,date(2026,10,1))
    assert rows[0]['financial_context']['status']=='missing'
    assert rows[0]['funding_window']['status']=='unknown'
    assert rows[0]['recommendation']['status']=='RESEARCH_WORTHY'
