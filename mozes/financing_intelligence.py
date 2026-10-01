"""Source-span-backed financing context; a shelf is not money raised.

Rules extract disclosure facts only. They do not infer net proceeds, future dilution,
share-count denominators, or a probability of an offering.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from urllib.parse import urlparse

from . import db
from .intelligence_store import digest, encode, ensure_schema, operation_finish, operation_start, utcnow, state_get, state_put

FORMS = ('S-3', 'S-3/A', 'S-3ASR', 'S-1', 'S-1/A', '424B3', '424B4', '424B5', '8-K', '6-K')
NUMBER = r'(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>million|billion|thousand)?'
MONEY = re.compile(r'(?<![A-Za-z])(?:US\s*)?\$\s*' + NUMBER, re.I)
SHARES = re.compile(NUMBER + r'\s+(?:shares of (?:its |our )?(?:common|ordinary) stock|common shares|ordinary shares)\b', re.I)
PRICE = re.compile(r'\$\s*(?P<number>\d[\d,]*(?:\.\d+)?)\s+per\s+(?:common\s+)?share\b', re.I)
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


def analyze_financing(form, text):
    form = form.upper().strip()
    low = text.lower()
    resale = bool(re.search(r'selling (?:stockholders|shareholders|securityholders)', low)
                  and re.search(r'(?:will not|do not|will receive no) receive (?:any )?proceeds|no proceeds to (?:us|the company)', low))
    atm = bool(re.search(r'at[- ]the[- ]market (?:offering|sales|equity)|sales agreement.*?sales agent', low))
    completed = bool(re.search(r'(?:has |have )?(?:closed|completed) (?:its |our |the |an? )?(?:(?:underwritten|public|registered direct|private) )*offering|closing of (?:its |the )?(?:public )?offering (?:occurred|was completed)', low))
    priced = bool(re.search(r'(?:announces?|announced|has announced) (?:the )?pricing of|priced (?:its |the |an? )?(?:public |underwritten )?offering', low))
    proposed = bool(re.search(r'(?:proposed|intends? to (?:offer|sell)|commenced|launch(?:ed)?) .{0,70}(?:offering|shares)|proposed (?:public )?offering', low))
    financing_language = bool(re.search(r'public offering|private placement|registered direct|securities purchase agreement|at[- ]the[- ]market', low))
    base_form = form.split('/')[0]
    if completed:
        for fragment in re.split(r'[.!?]\s+|\n', low):
            if re.search(r'completed|closed', fragment) and re.search(r'\bif\b|not (?:yet )?(?:completed|closed)|may (?:have )?(?:completed|closed)', fragment):
                completed = False
                break
    if completed and (proposed or priced):
        kind = 'multiple_transactions_review'
    elif resale:
        kind = 'resale_registration'
    elif completed:
        kind = 'completed_offering'
    elif priced:
        kind = 'priced_offering'
    elif proposed:
        kind = 'proposed_offering'
    elif atm:
        kind = 'atm_capacity'
    elif base_form in {'S-3', 'S-3ASR', 'S-1'}:
        kind = 'registration_only'
    elif base_form in {'424B3', '424B4', '424B5'} or financing_language:
        kind = 'financing_review_required'
    else:
        kind = 'not_identified'
    facts = []
    for m in MONEY.finditer(text):
        a, b, sentence = _sentence(text, m.start(), m.end())
        context = sentence.lower()
        if re.search(r'Canadian dollars|Australian dollars|\bCAD\b|\bAUD\b|C\$|A\$', text, re.I) and not m[0].upper().startswith('US'):
            continue
        # Strong anchors in the same sentence; no global largest-dollar-number heuristic.
        prefix = text[max(a, m.start()-150):m.start()].lower()
        anchors = [(hit.end(), kind) for expr, kind in (
            (r'gross proceeds', 'gross_proceeds'),
            (r'net proceeds', 'net_proceeds'),
            (r'up to|aggregate offering price|aggregate sales price', 'authorized_capacity'),
        ) for hit in re.finditer(expr, prefix)]
        if not anchors:
            continue
        fk = max(anchors)[1]
        if fk == 'authorized_capacity' and not re.search(r'sell|offering|shares|securities', context):
            continue
        # Per-share dollars in a proceeds sentence must not become proceeds totals.
        if re.match(r'\s+per\s+(?:common\s+)?share', text[m.end():m.end()+30], re.I):
            continue
        fact = _fact(text, m, fk, 'USD')
        fact['transaction_status'] = kind
        facts.append(fact)
    for regex, fk, unit in ((SHARES, 'offered_common_shares', 'shares'), (PRICE, 'offering_price_per_share', 'USD/share')):
        for m in regex.finditer(text):
            _, _, sentence = _sentence(text, m.start(), m.end())
            if not re.search(r'offer|sell|sale|sold|purchase', sentence, re.I):
                continue
            if fk == 'offering_price_per_share' and re.search(r'exercise price|warrant', sentence, re.I):
                continue
            facts.append(_fact(text, m, fk, unit))
    # Multiple figures are kept as candidates, not arbitrarily collapsed into one amount.
    facts = [dict(item, ambiguous=sum(x['kind'] == item['kind'] for x in facts) > 1) for item in facts]
    return {'kind': kind, 'form': form, 'facts': facts, 'source_text_hash': digest(text),
            'has_warrants': bool(re.search(r'\bwarrants?\b', low)),
            'cash_raised_verified': kind == 'completed_offering' and any(x['kind'] in {'gross_proceeds', 'net_proceeds'} and not x['ambiguous'] for x in facts),
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
    rid = 'OFFER-' + digest([ticker, accession, filing['url'], analysis['source_text_hash']])[:32]
    with conn:
        inserted = conn.execute('INSERT OR IGNORE INTO v2c_financing_events VALUES(?,?,?,?,?,?,?,?)',
                     (rid, ticker.upper(), accession, published, utcnow(), filing['url'], digest(text), encode(analysis))).rowcount
    return {**analysis, 'record_id': rid, 'source_url': filing['url'], 'published_at': published, 'new_record': bool(inserted)}


def financing_context(conn, ticker, *, as_of):
    ensure_schema(conn)
    cutoff = (as_of - timedelta(days=120)).isoformat()
    rows = conn.execute('SELECT * FROM v2c_financing_events WHERE ticker=? AND published_at>=? '
                        'AND substr(published_at,1,10)<=? ORDER BY published_at DESC,captured_at DESC',
                        (ticker.upper(), cutoff, as_of.isoformat())).fetchall()
    out, seen = [], set()
    for row in rows:
        rec = dict(row)
        # One filing can include overlapping primary/exhibit disclosures; prefer detailed facts.
        key = (rec['accession'], json.loads(rec['payload'])['kind'])
        if key in seen:
            continue
        seen.add(key)
        out.append({**json.loads(rec['payload']), 'source_url': rec['source_url'],
                    'published_at': rec['published_at'], 'accession': rec['accession']})
    return out[:12]


from .source_observability import observed


@observed
def refresh_financing(conn, *, today=None, max_filings=8, budget_seconds=120, recorder=None):
    from .ingest import edgar
    today = today or date.today()
    rid = operation_start(conn, 'financing')
    out = {'requested': len(db.watch_rows(conn)), 'processed': 0, 'filings_checked': 0, 'identified': 0,
           'errors': [], 'missing_cik': [], 'budget_exhausted': False}
    cutoff = (today - timedelta(days=45)).isoformat()
    deadline = time.monotonic() + budget_seconds
    for watch in db.watch_rows(conn):
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
            out['processed'] += 1
            filings = edgar.recent_filings_v2(watch['cik'], forms=FORMS, limit=max_filings)
            for filing in filings:
                if time.monotonic() >= deadline:
                    out['budget_exhausted'] = True
                    break
                if not cutoff <= filing.get('filed', '') <= today.isoformat():
                    continue
                documents = edgar.filing_documents(filing) if filing['form'] in {'8-K', '6-K'} else [{'url': filing['url']}]
                for document in documents[:4]:
                    text = edgar.html_to_text(edgar._get(document['url']))
                    out['filings_checked'] += 1
                    analyzed = store_financing(conn, watch['ticker'], {**filing, 'url': document['url']}, text)
                    if analyzed:
                        out['identified'] += 1
                        if analyzed['new_record'] and baselined:
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
    out['error_count'] = len(out['errors'])
    out['completeness_pct'] = round(100 * out['processed'] / out['requested'], 1) if out['requested'] else 100.0
    status = 'PARTIAL' if out['errors'] else 'INCOMPLETE' if out['missing_cik'] or out['budget_exhausted'] else 'OK'
    operation_finish(conn, rid, status, out)
    return {**out, 'status': status}
