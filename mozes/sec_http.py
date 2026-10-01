"""Bounded SEC GET transport with persistent, integrity-checked public-data caching.

An identifying SEC_USER_AGENT is still required. No credentials are cached or logged.
One process performs at most four requests/second; only transient failures are retried.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .source_observability import record

_LAST_REQUEST = 0.0
MAX_BYTES = 16 * 1024 * 1024


def ttl_for(url):
    parsed = urlparse(url)
    if '/Archives/edgar/data/' in parsed.path and not parsed.path.endswith('index.json'):
        return 365 * 86400
    if '/companyfacts/' in parsed.path or 'company_tickers' in parsed.path:
        return 86400
    if '/submissions/' in parsed.path:
        return 900
    return 3600


def _atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(raw)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def get_text(url, *, user_agent=None, cache_dir=None, ttl=None, opener=None,
             clock=None, sleeper=None, retries=2):
    global _LAST_REQUEST
    user_agent = user_agent if user_agent is not None else os.environ.get('SEC_USER_AGENT', '')
    if not user_agent.strip():
        raise RuntimeError('SEC_USER_AGENT is required; SEC collection is disabled')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'www.sec.gov', 'data.sec.gov'}:
        raise ValueError('SEC transport accepts official HTTPS SEC hosts only')
    opener = opener or urllib.request.urlopen
    clock, sleeper = clock or time.time, sleeper or time.sleep
    root = Path(cache_dir or os.environ.get('MOZES_HTTP_CACHE', '.monitor/http-cache'))
    key = hashlib.sha256(url.encode()).hexdigest()
    body_path, meta_path = root / (key + '.body'), root / (key + '.json')
    max_age = ttl_for(url) if ttl is None else ttl
    started = time.monotonic()
    try:
        meta = json.loads(meta_path.read_text())
        raw = body_path.read_bytes()
        age = clock() - float(meta['retrieved_epoch'])
        if (0 <= age <= max_age and meta['url'] == url
                and hashlib.sha256(raw).hexdigest() == meta['sha256']):
            record("sec", cache=True, duration_ms=(time.monotonic() - started) * 1000)
            return raw.decode('utf-8')
    except (OSError, ValueError, KeyError, UnicodeError):
        pass
    for attempt in range(retries + 1):
        delay = .25 - (time.monotonic() - _LAST_REQUEST)
        if delay > 0:
            sleeper(delay)
        _LAST_REQUEST = time.monotonic()
        request = urllib.request.Request(url, headers={'User-Agent': user_agent, 'Accept-Encoding': 'identity'})
        try:
            with opener(request, timeout=20) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('SEC response exceeds size budget')
            text = raw.decode('utf-8')
            if parsed.path.endswith('.json'):
                json.loads(text)  # never cache HTML error pages as API results
            _atomic(body_path, raw)
            _atomic(meta_path, json.dumps({'url': url, 'retrieved_epoch': clock(),
                    'sha256': hashlib.sha256(raw).hexdigest()}).encode())
            record("sec", cache=False, duration_ms=(time.monotonic() - started) * 1000)
            return text
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == retries:
                record("sec", cache=False, error=True, duration_ms=(time.monotonic() - started) * 1000)
                raise
            try:
                retry_after = float(exc.headers.get('Retry-After', 0))
            except (ValueError, AttributeError):
                retry_after = 0
            sleeper(min(30, max(2 ** attempt, retry_after)))
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                record("sec", cache=False, error=True, duration_ms=(time.monotonic() - started) * 1000)
                raise
            sleeper(2 ** attempt)
    raise RuntimeError('unreachable SEC retry state')
