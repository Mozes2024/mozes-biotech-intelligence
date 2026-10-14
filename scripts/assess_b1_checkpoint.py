"""Offline B1 counterfactual; never writes the frozen database or fetches sources.

Full documents are accepted only from an optional directory containing files named
<source_hash>.txt whose digest matches the immutable financing record. Otherwise
each archived fact context is assessed independently, never joined into a filing.
"""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

from mozes.financing_intelligence import analyze_financing, financing_context
from mozes.intelligence_store import digest

BASELINE = 'e98cb3c25b6c3a427bb06cd55b4189d72bf82762'


def assess(path, documents=None):
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    old = {'__name__': 'mozes._b1_baseline', '__package__': 'mozes'}
    source = subprocess.check_output(['git', 'show', BASELINE + ':mozes/financing_intelligence.py'], text=True)
    exec(compile(source, '<pinned-baseline>', 'exec'), old)
    old['ensure_schema'] = lambda conn: None  # Context reads only; do not initialize frozen tables.
    rows = conn.execute('SELECT * FROM v2c_financing_events').fetchall()
    full, excerpts, examples = [], [], []
    for row in rows:
        payload = json.loads(row['payload'])
        file = documents / (row['source_hash'] + '.txt') if documents else None
        text = file.read_text(encoding='utf-8') if file and file.exists() else None
        if text is not None:
            if digest(text) != row['source_hash']:
                raise ValueError('Document hash mismatch: ' + row['record_id'])
            new = analyze_financing(payload['form'], text)
            full.append({'ticker': row['ticker'], 'before': payload['kind'], 'after': new['kind'],
                         'review_required': new['classification_status'] == 'REVIEW_REQUIRED'})
        contexts = list(dict.fromkeys(f['context'] for f in payload.get('facts', [])))
        changed = []
        for context in contexts:
            baseline = old['analyze_financing'](payload['form'], context)
            new = analyze_financing(payload['form'], context)
            if (baseline['kind'], baseline['cash_raised_verified'], [(f['kind'], f['unit']) for f in baseline['facts']]) != (
                    new['kind'], new['cash_raised_verified'], [(f['kind'], f['unit']) for f in new['facts']]):
                changed.append({'before': baseline['kind'], 'after': new['kind'],
                                'review_required': new['classification_status'] == 'REVIEW_REQUIRED',
                                'facts': [{k: f[k] for k in ('kind', 'value', 'unit', 'realized')} for f in new['facts']]})
        excerpts.append({'ticker': row['ticker'], 'accession': row['accession'], 'context_count': len(contexts), 'changes': changed})
        if changed and row['ticker'] in {'MRNA', 'SMMT', 'ABVX', 'CRDL', 'AMLX', 'BIVI'}:
            examples.append({'ticker': row['ticker'], 'url': row['source_url'], 'changes': changed[:3]})
    context_changes = []
    for ticker in sorted({r['ticker'] for r in rows}):
        previous = old['financing_context'](conn, ticker, as_of=date(2026, 10, 13))
        current = financing_context(conn, ticker, as_of=date(2026, 10, 13))
        a, b = sum(len(x.get('facts', [])) for x in previous), sum(len(x.get('facts', [])) for x in current)
        if a != b: context_changes.append({'ticker': ticker, 'facts_before': a, 'facts_after': b})
    result = {'baseline': BASELINE, 'checkpoint_sha256': before,
              'integrity': conn.execute('PRAGMA integrity_check').fetchone()[0],
              'foreign_key_violations': len(conn.execute('PRAGMA foreign_key_check').fetchall()),
              'financing_records': len(rows), 'stored_classifications': dict(Counter(json.loads(r['payload'])['kind'] for r in rows)),
              'hash_verified_full_documents': len(full), 'whole_document_unresolved': len(rows)-len(full),
              'full_document_results': full, 'available_contexts': sum(r['context_count'] for r in excerpts),
              'disclosures_with_changed_excerpt_results': sum(bool(r['changes']) for r in excerpts),
              'changed_excerpt_count': sum(len(r['changes']) for r in excerpts),
              'changed_excerpts_requiring_review': sum(x['review_required'] for r in excerpts for x in r['changes']),
              'context_detail_changes': context_changes, 'examples': examples, 'excerpt_results': excerpts}
    conn.close()
    result['database_bytes_unchanged'] = before == hashlib.sha256(path.read_bytes()).hexdigest()
    if not result['database_bytes_unchanged']: raise RuntimeError('Frozen database changed')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('--documents', type=Path)
    args = parser.parse_args()
    print(json.dumps(assess(args.checkpoint, args.documents), indent=2, ensure_ascii=False))
