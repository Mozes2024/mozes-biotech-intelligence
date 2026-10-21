"""Diagnostic evidence must explain failures without disclosing credentials/state."""
import io
import json
import urllib.error

import pytest

from scripts import bio_continuity_control as control


def test_http_error_retains_codes_endpoint_ray_and_redacts_secrets(monkeypatch):
    secret = 'private-worker-credential-123456789'
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', secret)
    monkeypatch.setattr(control, 'proof', {})
    body = json.dumps({'errors': [{'code': 6003, 'message': 'Invalid request headers',
        'error_chain': [{'code': 6111, 'message': 'Invalid format for Authorization header ' + secret}]}],
        'result': {'bindings': [{'text': 'binding-content-must-not-appear'}]}}).encode()

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, 'Bad Request',
                {'CF-Ray': '0123456789abcdef-LHR'}, io.BytesIO(body))

    monkeypatch.setattr(control.urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(RuntimeError, match='HTTP_400'):
        control.cf(f'accounts/{control.ACCOUNT}/workers/scripts/{control.WORKER}/settings')
    row = control.proof['cloudflare_requests'][0]
    assert [error['code'] for error in row['errors']] == [6003, 6111]
    assert row['cf_ray'] == '0123456789abcdef-LHR'
    assert row['endpoint'].endswith('/mozes-hot-clock/settings')
    serialized = json.dumps(control.proof)
    assert secret not in serialized
    assert 'binding-content-must-not-appear' not in serialized
    assert control.ACCOUNT not in serialized


def test_structured_details_and_unexpected_secrets_are_not_logged(monkeypatch):
    monkeypatch.setenv('EDGE_SYNC_TOKEN', 'short-sensitive-value')
    assert 'short-sensitive-value' not in control.safe_reason('error short-sensitive-value')
    assert control.safe_reason('{"bindings":[{"text":"private"}]}') == 'structured error detail omitted'
    assert 'another-secret' not in control.safe_reason('unrecognized value "another-secret"')


def test_non_read_sql_rejected_before_network(monkeypatch):
    monkeypatch.setattr(control.urllib.request, 'build_opener', lambda *args: pytest.fail('network attempted'))
    with pytest.raises(RuntimeError, match='non_read_query_refused'):
        control.cf('unused', sql='DELETE FROM edge_events')


def test_diagnostic_checks_d1_independently_and_never_runs_recovery(monkeypatch):
    monkeypatch.setenv('CLOUDFLARE_ACCOUNT_ID', control.ACCOUNT)
    monkeypatch.setenv('CLOUDFLARE_RECOVERY_READ_TOKEN', 'existing-reader')
    monkeypatch.setattr(control, 'proof', {})
    monkeypatch.setattr(control.subprocess, 'run', lambda *args, **kwargs: pytest.fail('producer command attempted'))
    calls = []

    def fake_cf(path, **options):
        calls.append((path, options))
        control.proof.setdefault('cloudflare_requests', []).append({})
        if path.endswith('/settings'):
            raise RuntimeError('Cloudflare_Workers_Scripts_read_HTTP_400')
        if path.endswith('/query'):
            assert options['sql'] == 'SELECT name FROM d1_migrations ORDER BY id'
            if options['token_name'] == 'CLOUDFLARE_API_TOKEN':
                raise RuntimeError('Cloudflare_D1_SELECT_query_HTTP_403')
            return [{'success': True, 'results': [{'name': f'000{i}_schema.sql'} for i in range(1, 5)]}]
        if path.endswith('/deployments'):
            return {'deployments': []}
        if path.endswith('/schedules'):
            return {'schedules': []}
        return b'Worker bundle', 'application/javascript'

    monkeypatch.setattr(control, 'cf', fake_cf)
    with pytest.raises(RuntimeError, match='diagnostic_blocked'):
        control.diagnose()
    assert len(calls) == 6
    assert calls[-1][1]['token_name'] == 'CLOUDFLARE_RECOVERY_READ_TOKEN'
    assert control.proof['cloudflare_requests'][-1]['schema_check'] == 'PASS'
