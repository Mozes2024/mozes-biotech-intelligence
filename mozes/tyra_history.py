"""Reviewed historical TYRA records. Explicit import only; no network or dispatch.

Sources were reviewed on 2026-10-20. This module is never called by a live poll.
Run against a separate candidate DB; production import requires recovery review.
"""
from .live_monitor import record_change

IR = 'https://ir.tyra.bio/news-releases/news-release-details/'
SEC = 'https://www.sec.gov/Archives/edgar/data/1863127/'
RECORDS = (
    dict(accession='0001193125-26-385886', published_at='2026-09-09T00:00:00Z',
         headline='Tyra Biosciences reports initial Phase 2 SURF302 results',
         summary='Initial clinical results for dabogratinib; this is a completed data announcement.',
         event_status='results_reported',
         ir_url=IR + 'tyra-biosciences-reports-initial-phase-2-surf302-results',
         sec_url=SEC + '000119312526385886/d167841d8k.htm'),
    dict(accession='0001193125-26-390951', published_at='2026-09-14T00:00:00Z',
         headline='Tyra Biosciences announces pricing of an underwritten offering',
         summary='Approximately $400 million expected gross proceeds before expenses; closing remains conditional.',
         financing_status='priced', expected_gross_proceeds_usd=400_000_000,
         ir_url=IR + 'tyra-announces-pricing-400-million-underwritten-offering-common',
         sec_url=SEC + '000119312526390951/d102025d8k.htm'),
)


def import_history(conn):
    """Idempotent accession identity joins the reviewed IR and SEC variants."""
    identities = {(r[0], str(int(r[1]))) for r in conn.execute(
        "SELECT ticker,cik FROM sponsor_ticker_map WHERE ticker='TYRA' "
        "AND source='SEC-v2C-equity' AND confidence>=0.85 AND cik IS NOT NULL")}
    if identities != {('TYRA', '1863127')}:
        raise ValueError('unique verified TYRA SEC identity required')
    return [record_change(
        conn, ticker='TYRA', change_type='company_release_signal', previous_value=None,
        new_value={k: v for k, v in record.items() if k not in {'ir_url', 'sec_url'}},
        source_url=record['ir_url'], source_type='company_ir', severity='high',
        verification_state='primary_source', identity=['historical_sec_ir', '1863127', record['accession']],
        metadata={'historical_only': True, 'source_date_precision': 'day',
                  'source_published_at': record['published_at'],
                  'corroborating_sec_url': record['sec_url'], 'reviewed_at': '2026-10-20'},
    ) for record in RECORDS]
