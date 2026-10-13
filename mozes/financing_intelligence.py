"""Source-span-backed financing context; a shelf is not money raised.

Rules extract disclosure facts only. They do not infer net proceeds, future dilution,
share-count denominators, or a probability of an offering.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, timedelta, datetime, timezone
from urllib.parse import urlparse

from . import db
from .intelligence_store import digest, encode, ensure_schema, operation_finish, operation_start, utcnow, state_get, state_put

FORMS = ('S-3', 'S-3/A', 'S-3ASR', 'S-1', 'S-1/A', '424B3', '424B4', '424B5', '8-K', '6-K', '10-Q', '10-K', '20-F')
PERIODIC_FORMS = {'10-Q', '10-K', '20-F'}
NUMBER = r'(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>million|billion|thousand)?'
MONEY = re.compile(r'(?<![A-Za-z])(?P<currency>US\s*\$|U\.S\.\s*\$|C\$|A\$|(?:USD|CAD|AUD|NZD|HKD|SGD|CHF|JPY|CNY|INR|ZAR)\s*\$?|EUR\s*€?|GBP\s*£?|€|£|\$)\s*' + NUMBER, re.I)
SHARES = re.compile(NUMBER + r'\s+(?:shares of (?:its |our )?(?:common|ordinary) stock|common shares|ordinary shares)\b', re.I)
PRICE = re.compile(MONEY.pattern + r'\s+per\s+(?:(?:common|ordinary|preferred)\s+)?share\b', re.I)
SCALE = {'million': 1e6, 'billion': 1e9, 'thousand': 1e3, None: 1}


def _sentence(text, start, end):
    # Decimal points are not sentence boundaries.
    breaks = [m.end() for m in re.finditer(r'[.!?](?:\s+(?=[A-Z])|$)|\n', text)]
    a = max([0] + [x for x in breaks if x <= start])
    b = min([len(text)] + [x for x in breaks if x >= end])
    return a, b, text[a:b].strip()


def _fact(text, match, kind, unit):
    value = float(match['number'].replace(',', '')) * SCALE[(match.groupdict().get('scale') or '').lower() or None]
    a, b, sentence = _sentence(text, match.start(), match.end())
    return {'kind': kind, 'value': value, 'unit': unit,
            'span': {'start': match.start(), 'end': match.end(), 'text': match[0]},
            'context': text[a:b], 'context_start': a, 'context_end': b}


def _segments(text):
    protected = re.sub(r'\b(?:U\.S\.|Inc\.|Ltd\.)', lambda m: m[0].replace('.', '~'), text)
    boundaries = [0] + [m.end() for m in re.finditer(r'[.!?](?:\s+(?=[A-Z])|$)|\n\s*\n', protected)] + [len(text)]
    return [(a, b, text[a:b]) for a, b in zip(boundaries, boundaries[1:]) if text[a:b].strip()]


def _instrument(text):
    if re.search(r'at[- ]the[- ]market|\bATM\b', text, re.I): return 'ATM'
    if re.search(r'convertible|convertible notes', text, re.I): return 'CONVERTIBLE'
    if re.search(r'\bPIPE\b|private placement', text, re.I): return 'PIPE'
    if re.search(r'registered[- ]direct', text, re.I): return 'REGISTERED_DIRECT'
    if re.search(r'\bwarrants?\b', text, re.I): return 'WARRANT'
    return 'EQUITY'


def _status(text, form):
    low = text.lower()
    financing = bool(re.search(r'offering|placement|financing|securities purchase agreement|at[- ]the[- ]market|\bATM\b|warrant', low))
    atm = _instrument(text) == 'ATM'
    if financing and re.search(r'cancel(?:led|ed)|withdrawn|terminated', low): return 'cancelled_offering'
    if re.search(r'earlier offering|prior offering|historical financing', low): return 'historical_financing'
    if financing and re.search(r'\bif\b|\bwould\b', low): return 'conditional_financing'
    if financing and re.search(r'\b(?:not|never)\s+(?:yet\s+)?(?:closed|completed)|no\s+(?:closing|settlement)', low): return 'financing_review_required'
    if re.search(r'warrants? .{0,25}(?:exercised|exercise)',low) and re.search(r'\breceived\b.{0,70}(?:gross|net) proceeds',low) and not re.search(r'not|may|would|if|expected',low): return 'completed_offering'
    if atm and re.search(r'\b(?:sold|received|generated|raised)\b', low) and not re.search(r'\bno\s+shares|did not sell|not sold|may|will|expected', low): return 'atm_sales'
    closed = bool(re.search(r'\b(?:closed|completed)\b.{0,70}\b(?:offering|placement|financing|sale|issuance)\b|\b(?:offering|placement)\b.{0,35}\b(?:closed|completed)\b|announced (?:the )?closing of', low))
    if closed and not re.search(r'will|may|could|expected|subject to|not (?:yet )?(?:closed|completed)', low): return 'completed_offering'
    if re.search(r'pricing of|\bpriced\b', low) and financing: return 'priced_offering'
    if atm: return 'atm_capacity'
    if re.search(r'proposed|intends? to (?:offer|sell)|launch(?:ed)?|commenced|entered.{0,60}securities purchase agreement', low): return 'proposed_offering' if financing else 'not_identified'
    if re.search(r'will (?:close|complete)|expected.{0,25}(?:close|closing|completion)', low) and financing: return 'proposed_offering'
    if re.search(r'selling (?:stockholders|shareholders|securityholders)', low): return 'resale_registration'
    if re.search(r'capacity to (?:sell|offer)|authorized to (?:sell|offer)', low): return 'registration_only'
    if form.split('/')[0] in {'S-3', 'S-3ASR', 'S-1'}: return 'registration_only'
    return 'financing_review_required' if financing else 'not_identified'


def _currency(match, sentence, text):
    marker = re.sub(r'\s+', '', match['currency']).upper()
    if marker != '$':
        return {'C$':'CAD', 'A$':'AUD', '€':'EUR', '£':'GBP', 'US$':'USD', 'U.S.$':'USD'}.get(marker,marker.rstrip('$€£'))
    local = re.findall(r'\b(?:USD|CAD|AUD|EUR|GBP|NZD|HKD|SGD|CHF|JPY|CNY|INR|ZAR)\b|Canadian dollars|Australian dollars|U\.S\. dollars', sentence, re.I)
    units = { {'CANADIAN DOLLARS':'CAD','AUSTRALIAN DOLLARS':'AUD','U.S. DOLLARS':'USD'}.get(x.upper(),x.upper()) for x in local }
    if len(units) == 1: return units.pop()
    if units or re.search(r'Canadian|Australian|\b(?:CAD|AUD|NZD|HKD|SGD|CHF|JPY|CNY|INR|ZAR|EUR|GBP)\b|C\$|A\$|€|£', text, re.I): return 'UNKNOWN'
    return 'USD'  # Existing SEC dollar convention, never applied over an explicit foreign marker.


def _period(text):
    match = re.search(r'(?:months|quarter|year|period) ended\s+([A-Z][a-z]+\s+\d{1,2},?\s+20\d{2})', text, re.I)
    if not match: return None
    try: end = datetime.strptime(match[1].replace(',', ''), '%B %d %Y').date().isoformat()
    except ValueError: return None
    return {'end':end, 'text':match[0]}


def _economic_date(text):
    match=re.search(r'\b(?:on|in)\s+([A-Z][a-z]+\s+\d{1,2},?\s+20\d{2})',text,re.I)
    if not match: return None
    try: return datetime.strptime(match[1].replace(',', ''),'%B %d %Y').date().isoformat()
    except ValueError: return None


def analyze_financing(form, text):
    form = form.upper().strip()
    transactions, facts = [], []
    previous = None
    for a,b,sentence in _segments(text):
        status = _status(sentence, form)
        amount_only = (status == 'not_identified' or status == 'registration_only' and previous and previous['status']=='atm_capacity') and bool(re.search(r'gross proceeds|net proceeds|up to',sentence,re.I))
        if amount_only and previous and a - previous['last_end'] < 3:
            transaction = previous
        else:
            if status == 'not_identified' and form in {'424B3','424B4','424B5'}: status = 'financing_review_required'
            transaction = {'id':len(transactions),'status':status,'instrument':_instrument(sentence),
                           'recipient':'selling_shareholders' if re.search(r'selling (?:stockholders|shareholders|securityholders)',sentence,re.I) else 'issuer',
                           'reporting_period':_period(sentence), 'evidence':[], 'last_end':b}
            transaction['economic_date']=_economic_date(sentence)
            if re.search(r'\binvestors?\b.{0,30}receiv',sentence,re.I) and not re.search(r'proceeds to (?:us|the company|the issuer)',sentence,re.I):
                transaction['recipient']='unknown'
            transactions.append(transaction)
        transaction['evidence'].append({'start':a,'end':b,'text':sentence})
        transaction['last_end'] = b
        previous = transaction
        if re.search(r'(?:will not|do not) receive (?:any )?proceeds|(?:will )?receive no proceeds|no proceeds to (?:us|the company)',sentence,re.I):
            if len(transactions)>1 and transaction['status']=='not_identified':
                transactions[-2]['recipient']='selling_shareholders'
            transaction['recipient']='selling_shareholders'
        matches=list(MONEY.finditer(sentence))
        for local in matches:
            # Recover original match offsets, not normalized or synthesized source text.
            m=MONEY.match(text,a+local.start())
            if re.match(r'\s+per\s+(?:\w+\s+){0,4}(?:share|unit|ADS)',text[m.end():m.end()+70],re.I): continue
            prefix=text[max(a,m.start()-150):m.start()]
            anchors=[(hit.end(),label) for expression,label in [(r'gross proceeds','gross_proceeds'),(r'net proceeds','net_proceeds'),
                      (r'up to|aggregate offering price|aggregate sales price','authorized_capacity'),(r'aggregate principal amount','convertible_principal')]
                     for hit in re.finditer(expression,prefix,re.I)]
            trailing=re.match(r'\s*(?:in|of|as)?\s*(gross proceeds|net proceeds|aggregate principal amount)',text[m.end():b],re.I)
            fk=trailing[1].lower().replace(' ','_') if trailing else max(anchors)[1] if anchors else None
            if fk=='aggregate_principal_amount': fk='convertible_principal'
            if fk in {'gross_proceeds','net_proceeds'} and re.search(r'use.{0,40}proceeds.{0,150}(?:pay|repay|cost|purchase)',prefix,re.I): fk='use_of_proceeds'
            if not fk or (fk=='authorized_capacity' and not re.search(r'sell|offering|shares|securities|capacity',sentence,re.I)): continue
            fact=_fact(text,m,fk,_currency(m,sentence,text))
            fact.update(context=sentence,context_start=a,context_end=b,transaction_id=transaction['id'],
                        transaction_status=transaction['status'],currency=fact['unit'],reporting_period=transaction['reporting_period'],
                        amount_uncertain=bool(re.search(r'expect(?:s|ed)?|estimate(?:d)?|would|could|may|potential',text[a:m.start()],re.I) or
                                              re.match(r'\s*(?:[-–]|to)\s*\$?\d',text[m.end():b],re.I)))
            facts.append(fact)
        for regex,fk,unit in [(SHARES,'offered_common_shares','shares'),(PRICE,'offering_price_per_share','USD/share')]:
            for local in regex.finditer(sentence):
                if not re.search(r'offer|sell|sale|sold|purchase',sentence,re.I): continue
                if fk=='offering_price_per_share' and re.search(r'exercise price|warrant',sentence,re.I): continue
                if fk=='offering_price_per_share' and re.search(r'par value\s*$',sentence[max(0,local.start()-30):local.start()],re.I): continue
                m=regex.match(text,a+local.start());fact=_fact(text,m,fk,unit)
                if fk=='offering_price_per_share': fact['unit']=_currency(m,sentence,text)+'/share'
                fact.update(context=sentence,context_start=a,context_end=b,transaction_id=transaction['id'],transaction_status=transaction['status'])
                facts.append(fact)
    active=[t for t in transactions if t['status']!='not_identified']
    kinds={t['status'] for t in active}
    if any(t['recipient']=='selling_shareholders' for t in active) and not any(t['recipient']=='issuer' and t['status']=='completed_offering' for t in active):
        kind='resale_registration'
    elif len(kinds)>1: kind='multiple_transactions_review'
    else: kind=next(iter(kinds),'not_identified')
    if kind=='not_identified' and facts: kind='financing_review_required'
    for f in facts:
        t=transactions[f['transaction_id']]
        f['recipient']=t['recipient']
        f['ambiguous']=sum(x['kind']==f['kind'] and x['transaction_id']==f['transaction_id'] for x in facts)>1 or (
            'historical_financing' in kinds and sum(x['kind']==f['kind'] for x in facts)>1)
        f['realized']=bool(f['kind'] in {'gross_proceeds','net_proceeds'} and t['status'] in {'completed_offering','atm_sales'}
                           and t['recipient']=='issuer' and f['unit']!='UNKNOWN' and not f['ambiguous'] and not f.get('amount_uncertain'))
    realized=any(f['realized'] for f in facts)
    review=kind in {'financing_review_required','multiple_transactions_review','conditional_financing'} or any(
        f['ambiguous'] or f['unit']=='UNKNOWN' or (f.get('amount_uncertain') and f['transaction_status'] in {'completed_offering','atm_sales'}) for f in facts)
    review=review or any(t['recipient']=='unknown' for t in active)
    for t in transactions: t.pop('last_end',None)
    return {'kind': kind, 'form': form, 'facts': facts, 'transactions':active, 'source_text_hash': digest(text),
            'financing_version':'financing-b1-v1','classification_status':'REVIEW_REQUIRED' if review else 'KNOWN',
            'realized_proceeds_status':'AVAILABLE' if realized else 'NOT_VERIFIED',
            'net_proceeds_available':any(f['kind']=='net_proceeds' and f['realized'] for f in facts),
            'requires_financing_reconciliation':realized or any(t['status']=='completed_offering' and t['recipient']=='issuer' for t in active),
            'has_warrants': bool(re.search(r'\bwarrants?\b', text,re.I)),
            'cash_raised_verified': realized,
            'does_not_adjust_cash_balance': True, 'does_not_change_evidence_score': True,
            'unquantified_dilution': kind not in {'not_identified', 'resale_registration', 'registration_only'}}


def store_financing(conn, ticker, filing, text):
    ensure_schema(conn)
    parsed = urlparse(filing['url'])
    if parsed.scheme != 'https' or parsed.hostname not in {'www.sec.gov', 'data.sec.gov'}:
        raise ValueError('financing provenance must be an official SEC URL')
    analysis = analyze_financing(filing['form'], text)
    if analysis['kind'] == 'not_identified':
        return None
    accession = filing.get('accession')
    published = filing.get('accepted') or filing.get('filed')
    if not accession or not published:
        raise ValueError('financing accession and publication date required')
    source_cik=re.search(r'/data/(\d+)/',parsed.path)
    cik=str(filing.get('cik') or (source_cik[1] if source_cik else '')).lstrip('0') or None
    if source_cik and cik != source_cik[1].lstrip('0'):
        raise ValueError('financing CIK does not match SEC source URL')
    analysis['issuer_cik']=cik
    rid = 'OFFER-' + digest([ticker, accession, filing['url'], analysis['source_text_hash']])[:32]
    with conn:
        inserted = conn.execute('INSERT OR IGNORE INTO v2c_financing_events VALUES(?,?,?,?,?,?,?,?)',
                     (rid, ticker.upper(), accession, published, utcnow(), filing['url'], digest(text), encode(analysis))).rowcount
    return {**analysis, 'record_id': rid, 'source_url': filing['url'], 'published_at': published, 'new_record': bool(inserted)}


def financing_context(conn, ticker, *, as_of):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2c_financing_events'").fetchone(): return []
    cutoff = (as_of - timedelta(days=120)).isoformat()
    rows = conn.execute('SELECT * FROM v2c_financing_events WHERE ticker=? AND published_at>=? '
                        'ORDER BY published_at DESC,captured_at DESC',
                        (ticker.upper(), cutoff)).fetchall()
    boundary=datetime(as_of.year,as_of.month,as_of.day,tzinfo=timezone.utc)
    eligible=[]
    for row in rows:
        rec = dict(row)
        try:
            published=datetime.fromisoformat(rec['published_at'].replace('Z','+00:00'))
            captured=datetime.fromisoformat(rec['captured_at'].replace('Z','+00:00'))
            if (published.replace(tzinfo=timezone.utc) if published.tzinfo is None else published)>=boundary or captured.tzinfo is None or captured>=boundary: continue
        except (ValueError,TypeError): continue
        payload=json.loads(rec['payload'])
        eligible.append({**payload,'source_url':rec['source_url'],'published_at':rec['published_at'],
                         'accession':rec['accession'],'source_known_at':rec['captured_at'],'source_hash':rec['source_hash'],
                         'issuer_cik':payload.get('issuer_cik') or (re.search(r'/data/(\d+)/',rec['source_url'])[1] if re.search(r'/data/(\d+)/',rec['source_url']) else None)})
    out,seen=[],{}
    # Prefer source detail over latest empty exhibit; retain all provenance.
    for item in sorted(eligible,key=lambda x:len(x.get('facts',[])),reverse=True):
        dates=sorted({t.get('economic_date') or (t.get('reporting_period') or {}).get('end') for t in item.get('transactions',[]) if t.get('economic_date') or t.get('reporting_period')})
        key=(item['issuer_cik'],item['source_hash'],tuple(dates)) if dates else (item['accession'],item['kind'],item['source_hash'])
        source={k:item[k] for k in ('accession','source_url','source_hash','source_known_at','published_at')}
        if not item.get('facts'):
            detailed=next((other for other in out if other['accession']==item['accession'] and other['kind']==item['kind']),None)
            if detailed:
                detailed['sources'].append(source)
                continue
        if key in seen:
            seen[key]['sources'].append(source)
            continue
        item['sources']=[source];seen[key]=item;out.append(item)
    out.sort(key=lambda x:x['published_at'],reverse=True)
    # Unproven overlap across accessions is disclosed, never added as fresh cash.
    def amounts(item):
        return {(f['kind'],f['value'],f['unit']) for f in item.get('facts',[]) if f['kind'] in {'net_proceeds','gross_proceeds','convertible_principal','authorized_capacity'}}
    for item in out:
        item['overlap_review']=any(other is not item and (other['source_hash']==item['source_hash'] or
                                  amounts(item) & amounts(other)) for other in out)
    return out[:12]


def requires_reconciliation(disclosure, statement_date):
    """Reported receipt dates only; never add gross/net/capacity to cash."""
    if not statement_date: return False
    if disclosure.get('financing_version')!='financing-b1-v1': return disclosure.get('kind')=='completed_offering'
    if not disclosure.get('requires_financing_reconciliation'): return False
    for transaction in disclosure.get('transactions',[]):
        if transaction['status'] not in {'completed_offering','atm_sales'} or transaction['recipient']!='issuer': continue
        end=transaction.get('economic_date') or (transaction.get('reporting_period') or {}).get('end')
        if end is None or end>statement_date: return True
    return False


from .source_observability import observed


def _published_after_coverage(filing, started_at):
    """Publication, not first discovery or the ATM reporting period, is the boundary."""
    try:
        published = datetime.fromisoformat((filing.get('accepted') or filing['filed']).replace('Z', '+00:00'))
        started = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
        if started.tzinfo is None: return False
        # SEC date-only publication is conservatively available at UTC midnight.
        if published.tzinfo is None: published = published.replace(tzinfo=timezone.utc)
        return published > started
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


@observed
def refresh_financing(conn, *, today=None, max_filings=8, budget_seconds=120, recorder=None,
                      watches=None, observation_started=None, deadline=None):
    from .ingest import edgar
    today = today or date.today()
    rid = operation_start(conn, 'financing')
    watches = db.watch_rows(conn) if watches is None else watches
    out = {'requested': len(watches), 'processed': 0, 'filings_checked': 0, 'identified': 0,
           'errors': [], 'missing_cik': [], 'budget_exhausted': False, 'document_limit_reached': False}
    if observation_started:
        out.update(observation_started=observation_started, budget_seconds=budget_seconds,
                   max_filings=max_filings, cohort='priority')
    cutoff = (today - timedelta(days=45)).isoformat()
    bounded_requests = deadline is not None
    deadline = min(deadline, time.monotonic() + budget_seconds) if deadline is not None else time.monotonic() + budget_seconds
    request_options = {'deadline': deadline} if bounded_requests else {}
    for watch in watches:
        if time.monotonic() >= deadline:
            out['budget_exhausted'] = True
            break
        if not watch.get('cik'):
            out['missing_cik'].append(watch['ticker'])
            continue
        baseline_key = 'financing_baselined:' + watch['ticker']
        baselined = state_get(conn, baseline_key, False)
        error_count = len(out['errors'])
        try:
            coverage_key = 'financing_periodic_coverage_started:' + watch['ticker']
            coverage_started = state_get(conn, coverage_key)
            if coverage_started is None:
                coverage_started = observation_started or utcnow()
                # Persist before source I/O: failed/partial initial scans must not
                # turn previously published coverage into new changes on retry.
                state_put(conn, coverage_key, coverage_started)
            out['processed'] += 1
            if observation_started:
                state_put(conn, 'b1_financing_attempt:' + watch['ticker'], utcnow())
            filings = edgar.recent_filings_v2(watch['cik'], forms=FORMS, limit=max_filings, **request_options)
            for filing in filings[:max_filings]:
                if time.monotonic() >= deadline:
                    out['budget_exhausted'] = True
                    break
                if not cutoff <= filing.get('filed', '') <= today.isoformat():
                    continue
                documents = [{'url': filing['url']}]
                if filing['form'] in {'8-K', '6-K'}:
                    if observation_started:
                        documents, diagnostics = edgar.filing_documents(filing, return_diagnostics=True, **request_options)
                        out['errors'].extend({'ticker': watch['ticker'], 'reason': item['reason']}
                                             for item in diagnostics if item['status'] != 'OK')
                    else:
                        documents = edgar.filing_documents(filing)
                out['document_limit_reached'] |= len(documents) > 4
                for document in documents[:4]:
                    if time.monotonic() >= deadline:
                        out['budget_exhausted'] = True
                        break
                    text = edgar.html_to_text(edgar._get(document['url'], **request_options))
                    out['filings_checked'] += 1
                    analyzed = store_financing(conn, watch['ticker'], {**filing, 'url': document['url'], 'cik':watch['cik']}, text)
                    if analyzed:
                        out['identified'] += 1
                        new_publication = filing['form'] not in PERIODIC_FORMS or _published_after_coverage(filing, coverage_started)
                        emit = (_published_after_coverage(filing, observation_started) if observation_started
                                else baselined and new_publication)
                        if analyzed['new_record'] and emit:
                            if recorder is None:
                                from .live_monitor import record_change
                                recorder = record_change
                            recorder(conn, ticker=watch['ticker'], change_type='financing_interpreted',
                                          previous_value=None, new_value={'kind': analyzed['kind'], 'form': filing['form']},
                                          source_url=document['url'], source_type='sec', verification_state='derived',
                                          severity='medium' if analyzed['kind'] in {'proposed_offering', 'priced_offering'} else 'low',
                                          identity=['financing-v2c', analyzed['record_id']],
                                          metadata={'facts': analyzed['facts'], 'filed': filing['filed']})
            if len(out['errors']) == error_count and time.monotonic() < deadline:
                state_put(conn, baseline_key, True)
        except Exception as exc:
            out['errors'].append({'ticker': watch['ticker'], 'error': str(exc)[:200]})
            out['budget_exhausted'] |= time.monotonic() >= deadline
    out['error_count'] = len(out['errors'])
    out['completeness_pct'] = round(100 * out['processed'] / out['requested'], 1) if out['requested'] else 100.0
    status = 'PARTIAL' if out['errors'] else 'INCOMPLETE' if out['missing_cik'] or out['budget_exhausted'] or out['document_limit_reached'] else 'OK'
    operation_finish(conn, rid, status, out)
    return {**out, 'status': status}


def refresh_due_financing(conn, *, now=None):
    """One bounded priority batch per six hours, owned by the hot monitor DB."""
    from .priority import priority_tickers
    if not os.environ.get('SEC_USER_AGENT', '').strip():
        return {'status': 'SKIPPED', 'reason': 'SEC_USER_AGENT missing'}
    ensure_schema(conn)
    now = now or datetime.now(timezone.utc)
    observation = state_get(conn, 'b1_financing_observation', {})
    if observation.get('last_attempt'):
        last = datetime.fromisoformat(observation['last_attempt'])
        if now - last < timedelta(hours=6):
            return {'status': 'SKIPPED', 'reason': 'not_due'}
    priority = set(priority_tickers())
    watches = [row for row in db.watch_rows(conn) if row['ticker'] in priority
               and str(row.get('cik') or '').isdigit()]
    if not watches:
        return {'status': 'SKIPPED', 'reason': 'no_priority_cik'}
    watches.sort(key=lambda row: (state_get(conn, 'b1_financing_attempt:' + row['ticker'], ''), row['ticker']))
    started = observation.get('started_at') or now.isoformat()
    # Commit before I/O: a partial/failed attempt cannot retry on every hot tick
    # or move the new-publication boundary. Unattempted issuers retain priority.
    state_put(conn, 'b1_financing_observation', {'started_at': started, 'last_attempt': now.isoformat()})
    return refresh_financing(conn, today=now.date(), watches=watches[:5], max_filings=8,
                             budget_seconds=60, deadline=time.monotonic() + 60,
                             observation_started=started)
