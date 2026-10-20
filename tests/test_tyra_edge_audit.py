import importlib.util
from pathlib import Path
import urllib.error
import pytest

spec = importlib.util.spec_from_file_location('tyra_audit', Path(__file__).resolve().parents[1] / 'scripts/inspect_tyra_edge.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_absent_event_is_a_retention_limitation_not_missing_identity():
    paths = []
    def read(path):
        paths.append(path)
        raise urllib.error.HTTPError('https://trusted.example', 404, 'absent', None, None)
    result = audit.inspect(read=read)
    assert len(paths) == 2 and all(path.startswith('event?id=EDGE-') for path in paths)
    assert all(row['retained'] is False for row in result['events'])
    assert 'purges' in result['limitation']


def test_authentication_failure_never_becomes_absence_or_echoes_token():
    def read(path):
        raise urllib.error.HTTPError('https://trusted.example', 403, 'private-token', None, None)
    with pytest.raises(RuntimeError, match='unavailable') as exc:
        audit.inspect(read=read)
    assert 'private-token' not in str(exc.value)


def test_wrong_issuer_fails_closed():
    with pytest.raises(RuntimeError, match='identity mismatch'):
        audit.inspect(read=lambda path: {'ticker': 'OTHER'})
