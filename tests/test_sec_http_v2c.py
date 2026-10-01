import io
import urllib.error

import pytest
from mozes.sec_http import get_text


class Response(io.BytesIO):
    pass


def test_cache_reuses_response_and_does_not_store_identity(tmp_path):
    calls = []
    def fetch(request, timeout):
        calls.append(request.full_url)
        return Response(b'{"facts":{}}')
    kwargs = dict(user_agent='Private Name person@example.invalid', cache_dir=tmp_path,
                  opener=fetch, clock=lambda: 100, sleeper=lambda _: None)
    url = 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000000123.json'
    assert get_text(url, **kwargs) == '{"facts":{}}'
    assert get_text(url, **kwargs) == '{"facts":{}}'
    assert len(calls) == 1
    assert all('person@' not in p.read_text() for p in tmp_path.iterdir())


def test_missing_identity_no_network(tmp_path):
    with pytest.raises(RuntimeError):
        get_text('https://data.sec.gov/x.json', user_agent='', cache_dir=tmp_path)


def test_429_retries_but_403_does_not(tmp_path):
    count = [0]
    def fetch(request, timeout):
        count[0] += 1
        if count[0] == 1:
            raise urllib.error.HTTPError(request.full_url, 429, 'rate', {'Retry-After':'1'}, None)
        return Response(b'{}')
    get_text('https://data.sec.gov/x.json', user_agent='test', opener=fetch, cache_dir=tmp_path,
             sleeper=lambda _:None)
    assert count[0] == 2
    def forbidden(request, timeout):
        count[0] += 1
        raise urllib.error.HTTPError(request.full_url, 403, 'forbidden', {}, None)
    with pytest.raises(urllib.error.HTTPError):
        get_text('https://data.sec.gov/y.json', user_agent='test', opener=forbidden, cache_dir=tmp_path,
                 sleeper=lambda _:None)
    assert count[0] == 3


def test_invalid_json_not_cached(tmp_path):
    with pytest.raises(ValueError):
        get_text('https://data.sec.gov/x.json', user_agent='test', opener=lambda *a, **k: Response(b'<html>Error</html>'),
                 cache_dir=tmp_path, sleeper=lambda _: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('url', ['http://www.sec.gov/x', 'https://sec.gov.evil/x', 'https://example.com'])
def test_host_allowlist(url, tmp_path):
    with pytest.raises(ValueError):
        get_text(url, user_agent='test', cache_dir=tmp_path)
