import json
from pathlib import Path
import pytest
from mozes.financing_intelligence import analyze_financing

CASES=json.loads((Path(__file__).parent/'fixtures/b1/financing_contract.json').read_text())

@pytest.mark.parametrize('case',CASES,ids=lambda case:case['id'])
def test_financing_disclosure_contract(case):
    result=analyze_financing(case['form'],case['text'])
    assert result['kind']==case['kind']
    assert result['cash_raised_verified']==case['cash_verified']
    assert [{k:fact[k] for k in ('kind','value','unit')} for fact in result['facts']]==case['facts']
    assert result['does_not_adjust_cash_balance'] and result['does_not_change_evidence_score']
    for fact in result['facts']:
        assert case['text'][fact['span']['start']:fact['span']['end']]==fact['span']['text']
        assert case['text'][fact['context_start']:fact['context_end']]==fact['context']


def test_amount_range_and_explicit_usd_are_not_misrepresented():
    amount=analyze_financing('8-K','We completed an offering with gross proceeds of $20-25 million.')
    assert amount['classification_status']=='REVIEW_REQUIRED' and not amount['cash_raised_verified']
    usd=analyze_financing('6-K','We completed an offering with net proceeds of USD 20 million.')
    assert usd['facts'][0]['unit']=='USD' and usd['net_proceeds_available']
    price=analyze_financing('6-K','We priced an offering of ordinary shares at C$5 per share.')
    assert next(f for f in price['facts'] if f['kind']=='offering_price_per_share')['unit']=='CAD/share'


def test_atm_period_and_receipts_remain_separate_from_capacity():
    text='We sold shares under an at-the-market program during the three months ended June 30, 2026, generating net proceeds of $11 million.'
    result=analyze_financing('10-Q',text)
    fact=result['facts'][0]
    assert result['kind']=='atm_sales' and fact['realized']
    assert fact['reporting_period']['end']=='2026-06-30'
    assert result['net_proceeds_available'] and result['does_not_adjust_cash_balance']


def test_no_borrowed_completion_for_a_second_expected_offering():
    result=analyze_financing('8-K','We completed an offering with gross proceeds of $20 million. Separately, we priced another offering for expected net proceeds of $15 million.')
    assert result['classification_status']=='REVIEW_REQUIRED'
    assert [f['realized'] for f in result['facts']]==[True,False]


def test_duplicate_exhibits_and_amendment_keep_all_sources_without_double_count(tmp_path,monkeypatch):
    from datetime import date
    from mozes import db
    from mozes.financing_intelligence import store_financing,financing_context
    monkeypatch.setattr('mozes.financing_intelligence.utcnow',lambda:'2026-08-04T12:00:00+00:00')
    conn=db.connect(tmp_path/'b1.db')
    text='On July 31, 2026, we completed an offering with net proceeds of $20 million.'
    for accession,url,form in [('one','primary.htm','8-K'),('one','ex99.htm','8-K'),('two','amendment.htm','8-K/A')]:
        filing={'form':form,'accession':accession,'accepted':'2026-08-01T12:00:00Z','cik':'123',
                'url':'https://www.sec.gov/Archives/edgar/data/123/'+accession+'/'+url}
        first=store_financing(conn,'AAA',filing,text)
        assert first['issuer_cik']=='123'
        assert not store_financing(conn,'AAA',filing,text)['new_record']
    before=list(conn.iterdump())
    result=financing_context(conn,'AAA',as_of=date(2026,8,5))
    assert len(result)==1 and len(result[0]['sources'])==3
    assert result[0]['facts'][0]['value']==20e6 and result[0]['source_hash']==result[0]['source_text_hash']
    assert list(conn.iterdump())==before
    with pytest.raises(ValueError,match='CIK'):
        store_financing(conn,'AAA',{**filing,'cik':'999'},text)


def test_distinct_transactions_in_same_accession_are_not_dropped(tmp_path,monkeypatch):
    from datetime import date
    from mozes import db
    from mozes.financing_intelligence import store_financing,financing_context
    monkeypatch.setattr('mozes.financing_intelligence.utcnow',lambda:'2026-08-04T12:00:00+00:00')
    conn=db.connect(tmp_path/'b1.db')
    for i,value in enumerate([20,30]):
        store_financing(conn,'AAA',{'form':'8-K','accession':'same','filed':'2026-08-01',
            'url':f'https://www.sec.gov/Archives/edgar/data/123/same/ex{i}.htm'},
            f'We completed the offering with net proceeds of ${value} million.')
    result=financing_context(conn,'AAA',as_of=date(2026,8,5))
    assert len(result)==2 and {r['facts'][0]['value'] for r in result}=={20e6,30e6}


def test_different_exhibit_text_with_same_amount_requires_overlap_review(tmp_path,monkeypatch):
    from datetime import date
    from mozes import db
    from mozes.financing_intelligence import store_financing,financing_context
    monkeypatch.setattr('mozes.financing_intelligence.utcnow',lambda:'2026-08-04T12:00:00Z')
    conn=db.connect(tmp_path/'overlap.db')
    for i,prefix in enumerate(['We','The company']):
        store_financing(conn,'AAA',{'form':'8-K','accession':str(i),'filed':'2026-08-01',
            'url':f'https://www.sec.gov/Archives/edgar/data/123/{i}/ex.htm'},
            f'{prefix} completed the offering with net proceeds of $20 million.')
    rows=financing_context(conn,'AAA',as_of=date(2026,8,5))
    assert len(rows)==2 and all(r['overlap_review'] for r in rows)
    assert all(r['does_not_adjust_cash_balance'] for r in rows)


@pytest.mark.parametrize('published,captured',[
    ('2026-08-05','2026-08-04T12:00:00Z'),
    ('2026-08-01','2026-08-05T12:00:00Z'),
    ('2026-08-01','2026-08-04T12:00:00'),
])
def test_financing_context_rejects_same_day_and_unknown_source_availability(tmp_path,monkeypatch,published,captured):
    from datetime import date
    from mozes import db
    from mozes.financing_intelligence import store_financing,financing_context
    monkeypatch.setattr('mozes.financing_intelligence.utcnow',lambda:captured)
    conn=db.connect(tmp_path/'b1.db')
    store_financing(conn,'AAA',{'form':'8-K','accession':'one','filed':published,
        'url':'https://www.sec.gov/Archives/edgar/data/123/one/ex.htm'},'We completed the offering with net proceeds of $20 million.')
    assert financing_context(conn,'AAA',as_of=date(2026,8,5))==[]


def test_reconciliation_uses_reported_period_not_late_publication():
    from mozes.financing_intelligence import requires_reconciliation
    past=analyze_financing('10-Q','During the three months ended June 30, 2026, we sold shares under our ATM program with net proceeds of $11 million.')
    assert not requires_reconciliation(past,'2026-06-30')
    assert requires_reconciliation(past,'2026-03-31')
    gross=analyze_financing('8-K','We completed an offering with gross proceeds of $50 million.')
    assert requires_reconciliation(gross,'2026-06-30') and not gross['net_proceeds_available']


def test_principal_and_use_of_proceeds_are_not_cash_receipts():
    principal=analyze_financing('8-K','We completed an offering of $3 billion aggregate principal amount of convertible notes.')
    assert principal['facts'][0]['kind']=='convertible_principal'
    assert not principal['cash_raised_verified']
    use=analyze_financing('8-K','We expect to use net proceeds to pay $12 million for capped call transactions.')
    assert use['facts'][0]['kind']=='use_of_proceeds' and not use['cash_raised_verified']
    receipt=analyze_financing('8-K','We completed the offering with net proceeds of $20 million after deducting estimated expenses.')
    assert receipt['net_proceeds_available']


def test_shareholder_proceeds_negation_and_prices_are_not_issuer_receipts():
    resale=analyze_financing('8-K','We completed the offering with gross proceeds of $20 million. We will receive no proceeds from the sale.')
    assert not resale['cash_raised_verified'] and resale['kind']=='resale_registration'
    priced=analyze_financing('424B5','We offered shares, par value $0.01 per share, at $5 per share and $10 per American Depositary Share, up to $100 million.')
    assert [f['value'] for f in priced['facts'] if f['kind']=='offering_price_per_share']==[5]
    assert [f['value'] for f in priced['facts'] if f['kind']=='authorized_capacity']==[100e6]


def test_explicit_warrant_exercise_receipt_is_distinct_from_potential_exercise():
    result=analyze_financing('8-K','Warrants were exercised and we received net proceeds of $3 million.')
    assert result['net_proceeds_available'] and result['transactions'][0]['instrument']=='WARRANT'
    potential=analyze_financing('8-K','If warrants are exercised, we would receive gross proceeds of $3 million.')
    assert not potential['cash_raised_verified']


@pytest.mark.parametrize('currency',['NZD','HKD','SGD','CHF','JPY','CNY','INR','ZAR'])
def test_other_explicit_currencies_are_retained_without_conversion(currency):
    result=analyze_financing('6-K',f'We completed an offering with net proceeds of {currency} 20 million.')
    assert result['facts'][0]['unit']==currency
    assert result['facts'][0]['value']==20e6 and result['net_proceeds_available']
