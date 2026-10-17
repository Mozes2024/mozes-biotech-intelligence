"""Explicit offline staging entry point. Never fetches sources or opens the live DB.

Usage: python -m scripts.stage_price_response --database /scratch/staging.db
       --response response.json --declaration declaration.json
Both activation controls default off. This command only needs STAGING_ENABLED=1;
series selection/manifests are separately gated library operations.
"""
import argparse
import json
from pathlib import Path

from mozes.ingest.price_provenance import MAX_RESPONSE_BYTES, prepare_import
from mozes.price_staging import connect, gate, stage_imports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--response', required=True)
    parser.add_argument('--declaration', required=True)
    args = parser.parse_args()
    gate('STAGING', None)
    # Read at most the limit plus one; do not load an unbounded file into memory.
    with Path(args.response).open('rb') as fh:
        body = fh.read(MAX_RESPONSE_BYTES + 1)
    with Path(args.declaration).open('rb') as fh:
        declaration_bytes = fh.read(16385)
    if len(declaration_bytes) > 16384:
        raise ValueError('declaration_byte_budget')
    declaration = json.loads(declaration_bytes)
    prepared = prepare_import(body=body, **declaration)
    conn = connect(args.database)
    try:
        stage_imports(conn, [prepared])
    finally:
        conn.close()
    print(json.dumps({'status': 'staged', 'run_id': prepared['run']['run_id'],
                      'observations': len(prepared['rows']), 'legacy_prices_written': False}))


if __name__ == '__main__':
    main()
