"""Conservative issuer/security projection without sponsor-name or last-row wins.

The full candidate relation is (CIK,ticker). The legacy sponsor map is only a compatible
projection of unambiguous primary US equity choices. ADR/ADS are allowed, not silently
mapped to inaccessible foreign ordinary shares. Letter suffixes alone prove nothing.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from . import db
from .intelligence_store import encode, ensure_schema, utcnow
from .universe import normalize_org

DERIVATIVE = re.compile(r'\bwarrants?\b|\bunits?\b|\brights?\b|\bpreferred\b|\bdebentures?\b|\bnotes? due\b', re.I)
EQUITY = re.compile(r'\bcommon (?:stock|shares?)\b|\bordinary shares?\b|\bamerican deposit(?:ary|ory) (?:shares?|receipts?)\b|\bADS\b|\bADR\b', re.I)
EXCHANGES = {'NASDAQ', 'NYSE', 'NYSE AMERICAN', 'NYSEAMERICAN', 'AMEX', 'N', 'A', 'P', 'Z', 'V'}


def security_kind(listing):
    name = listing.get('name') or listing.get('security_name') or ''
    if listing.get('etf') == 'Y' or listing.get('test_issue') == 'Y':
        return 'excluded'
    if DERIVATIVE.search(name):
        return 'non_common_security'
    if EQUITY.search(name):
        return 'us_equity'
    return 'unknown_security_type'


def select_primary(rows, listings, *, verified_watch=None):
    verified_watch = set(verified_watch or [])
    by_ticker = {str(x.get('ticker', '')).upper(): x for x in listings}
    groups = defaultdict(list)
    for raw in rows:
        ticker = str(raw.get('ticker') or '').upper()
        cik = str(raw.get('cik') or raw.get('cik_str') or '').lstrip('0')
        name = raw.get('name') or raw.get('title') or raw.get('company') or ''
        if not ticker or not cik or not cik.isdigit() or not name:
            continue
        listing = by_ticker.get(ticker, {})
        kind = security_kind(listing) if listing else 'unknown_security_type'
        exchange = str(listing.get('exchange') or raw.get('exchange') or '').upper()
        eligible = kind == 'us_equity' and exchange in EXCHANGES
        # Verified watch membership disambiguates eligible share classes only.
        curated = False  # Unknown instrument descriptions never become eligible automatically.
        groups[cik].append({'cik': cik, 'ticker': ticker, 'sponsor': name,
                            'sponsor_norm': normalize_org(name), 'eligibility': 'eligible' if eligible or curated else kind,
                            'exchange': exchange, 'instrument_kind': kind,
                            'curated_equity_fallback': curated,
                            'source_url': listing.get('source_url') or 'https://www.sec.gov/files/company_tickers_exchange.json'})
    ticker_ciks = defaultdict(set)
    for candidates in groups.values():
        for item in candidates:
            ticker_ciks[item['ticker']].add(item['cik'])
    for candidates in groups.values():
        for item in candidates:
            if len(ticker_ciks[item['ticker']]) > 1:
                item['eligibility'] = 'conflicting_issuer_for_symbol'
    all_rows, selected, ambiguous = [], [], []
    for cik, candidates in sorted(groups.items()):
        # Duplicate SEC rows are not independent candidates.
        candidates = list({x['ticker']: x for x in candidates}.values())
        all_rows.extend(candidates)
        equity = [x for x in candidates if x['eligibility'] == 'eligible']
        curated = [x for x in equity if x['ticker'] in verified_watch]
        pick = curated[0] if len(curated) == 1 else equity[0] if len(equity) == 1 else None
        if pick:
            selected.append(pick)
        else:
            ambiguous.append({'cik': cik, 'tickers': sorted(x['ticker'] for x in equity),
                              'reason': 'ambiguous_share_classes' if len(equity) > 1 else 'no_verified_equity'})
    # Two distinct issuers can normalize to the same name. Neither wins by ordering.
    norm_groups = defaultdict(list)
    for row in selected:
        norm_groups[row['sponsor_norm']].append(row)
    projection = []
    for name, candidates in norm_groups.items():
        if name and len({x['cik'] for x in candidates}) == 1:
            projection.append(candidates[0])
        else:
            ambiguous.append({'sponsor_norm': name, 'reason': 'normalized_name_collision',
                              'tickers': [x['ticker'] for x in candidates]})
    return {'candidates': all_rows, 'projection': sorted(projection, key=lambda x: x['ticker']), 'ambiguous': ambiguous}


def update_company_map(conn, rows, listings):
    ensure_schema(conn)
    verified = {x['ticker'] for x in db.watch_rows(conn)
                if (db.security_lifecycle_row(conn, x['ticker']) or {}).get('status') == 'ACTIVE'}
    result = select_primary(rows, listings, verified_watch=verified)
    now = utcnow()
    with conn:
        conn.execute('DELETE FROM v2c_security_candidates')
        for row in result['candidates']:
            conn.execute('INSERT INTO v2c_security_candidates VALUES(?,?,?,?,?,?,?,?)',
                         (row['cik'], row['ticker'], row['sponsor'], row['sponsor_norm'], row['eligibility'],
                          row['source_url'], now, encode(row)))
        # Remove the old SEC-derived projection, including collisions/warrants selected by v2.
        conn.execute("DELETE FROM sponsor_ticker_map WHERE source LIKE 'SEC %' OR source='SEC-v2C-equity'")
        valid_issuers = {(row['cik'], row['ticker']) for row in result['projection']}
        for alias in conn.execute("SELECT sponsor_norm,cik,ticker FROM sponsor_ticker_map WHERE source LIKE 'SEC-v2E-subsidiary|%'").fetchall():
            if (str(alias['cik']).lstrip('0'), alias['ticker']) not in valid_issuers:
                conn.execute('DELETE FROM sponsor_ticker_map WHERE sponsor_norm=?', (alias['sponsor_norm'],))
        for row in result['projection']:
            existing = conn.execute('SELECT cik,ticker,source FROM sponsor_ticker_map WHERE sponsor_norm=?',
                                    (row['sponsor_norm'],)).fetchone()
            if existing and (str(existing['cik']).lstrip('0'), existing['ticker']) != (row['cik'], row['ticker']):
                # Retain manual mapping, but disable its automatic use pending reconciliation.
                conn.execute('UPDATE sponsor_ticker_map SET confidence=0 WHERE sponsor_norm=?', (row['sponsor_norm'],))
                continue
            conn.execute('INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?) '
                         'ON CONFLICT(sponsor_norm) DO UPDATE SET ticker=excluded.ticker,cik=excluded.cik,'
                         'confidence=excluded.confidence,source=excluded.source,updated_at=excluded.updated_at',
                         (row['sponsor_norm'], row['sponsor'], row['ticker'], row['cik'], .90, 'SEC-v2C-equity', now))
        # Resolve CIK by exact symbol; never copy another security's price or recommendation.
        by_ticker = {row['ticker']: row for row in result['candidates']}
        for watch in db.watch_rows(conn):
            match = by_ticker.get(watch['ticker'])
            if match and match['eligibility'] == 'eligible':
                conn.execute('UPDATE watch_universe SET cik=? WHERE ticker=?', (match['cik'], watch['ticker']))
            else:
                # Do not carry a stale/ambiguous issuer key into financial extraction.
                conn.execute('UPDATE watch_universe SET cik=NULL WHERE ticker=?', (watch['ticker'],))
    return result
