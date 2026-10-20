"""Read-only retained Edge traces for reviewed TYRA accessions; no processing."""
import hashlib
import json
import urllib.error
import urllib.request

from mozes.edge_enrichment import edge_request
from mozes.monitor_recovery import validate_verification_configuration

ACCESSIONS = ('0001193125-26-385886', '0001193125-26-390951')
FIELDS = ('event_id', 'source', 'ticker', 'cik', 'accession', 'first_seen_at',
          'analysis_status', 'material', 'lifecycle', 'suppression_reason',
          'enrichment_attempts', 'enrichment_dispatch_at', 'enrichment_ack_at',
          'enrichment_change_id')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def inspect(*, read=None):
    if read is None:
        validate_verification_configuration()
        opener = urllib.request.build_opener(NoRedirect()).open
        read = lambda path: edge_request(path, opener=opener)
    result = []
    for accession in ACCESSIONS:
        event_id = 'EDGE-' + hashlib.sha256(('sec:' + accession).encode()).hexdigest()[:24]
        try:
            event = read('event?id=' + event_id)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise RuntimeError('private TYRA trace unavailable') from None
            result.append({'accession': accession, 'event_id': event_id, 'retained': False})
            continue
        if (event.get('event_id') != event_id or event.get('accession') != accession
                or event.get('ticker') != 'TYRA' or str(event.get('cik', '')).lstrip('0') != '1863127'):
            raise RuntimeError('private TYRA trace identity mismatch')
        result.append({'retained': True, **{key: event.get(key) for key in FIELDS}})
    return {'status': 'READ_ONLY_TYRA_TRACE', 'events': result,
            'limitation': 'Absence means not retained under these SEC identities; no claim about prior purges or other sources.'}


if __name__ == '__main__':
    try:
        print(json.dumps(inspect()))
    except (OSError, ValueError, RuntimeError):
        raise SystemExit('private TYRA trace could not be verified') from None
