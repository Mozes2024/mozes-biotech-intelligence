"""Compare offline checkpoint copies; MOZES_PRICE_SHADOW_ENABLED=1 is required."""
import argparse
import json
from pathlib import Path

from mozes.price_shadow import compare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy', required=True)
    parser.add_argument('--staging', required=True)
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    with Path(args.request).open('rb') as handle:
        body = handle.read(65537)
    if len(body) > 65536:
        raise ValueError('request_byte_budget')
    request = json.loads(body)
    print(json.dumps(compare(args.legacy, args.staging, request['stock'], request['benchmark']), sort_keys=True))


if __name__ == '__main__':
    main()
