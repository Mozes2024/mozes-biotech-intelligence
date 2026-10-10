"""Bounded exact-source wire transport; access denials are never retried."""
import time
import hashlib
import json
import math
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


def retry_wait(value, *, now=None):
    if not value:
        return 2.0
    try:
        seconds = float(value)
    except ValueError:
        seconds = (parsedate_to_datetime(value) - (now or datetime.now(timezone.utc))).total_seconds()
    if not 0 <= seconds <= 10:
        return None  # Do not retry earlier than the publisher requested.
    return seconds


def fetch_wire(url, *, validate_redirect, user_agent, opener=None, sleeper=time.sleep):
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "text/html"})
    for attempt in range(2):
        try:
            with opener(request, timeout=20) as response:
                if urlsplit(response.geturl()).scheme != "https":
                    raise ValueError("primary wire redirect must remain HTTPS")
                validate_redirect(response.geturl())
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise ValueError("primary wire document too large")
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if attempt or exc.code not in (500, 502, 503, 504):
                raise
            try:
                wait = retry_wait((exc.headers or {}).get("Retry-After"))
            except (TypeError, ValueError, OverflowError):
                raise exc
            if wait is None:
                raise
        except TimeoutError:
            if attempt:
                raise
            wait = 2.0
        except urllib.error.URLError as exc:
            if attempt or not isinstance(exc.reason, TimeoutError):
                raise
            wait = 2.0
        sleeper(wait)


def fetch_with_cooldown(conn, url, *, fetch, now=None):
    """Keep publisher backoff in the same durable lineage as monitor observations."""
    from .live_monitor import observe
    now = now or datetime.now(timezone.utc)
    key = "wire_source_backoff:" + hashlib.sha256(url.encode()).hexdigest()
    prior = conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key=?", (key,)).fetchone()
    if prior and datetime.fromisoformat(json.loads(prior[0])["retry_at"]) > now:
        raise RuntimeError("primary source backoff active; event remains pending")
    try:
        return fetch(url)
    except (urllib.error.HTTPError, TimeoutError, urllib.error.URLError) as exc:
        seconds = 900
        error = "transport_timeout" if isinstance(exc, TimeoutError) else "transport_error"
        if isinstance(exc, urllib.error.HTTPError):
            error = f"HTTP {exc.code}"
            seconds = 3600 if exc.code in (403, 429) else 900
            value = (exc.headers or {}).get("Retry-After")
            if value:
                try:
                    parsed = float(value)
                    if math.isfinite(parsed):
                        seconds = max(seconds, parsed)
                except ValueError:
                    try:
                        seconds = max(seconds, (parsedate_to_datetime(value) - now).total_seconds())
                    except (TypeError, ValueError, OverflowError):
                        pass
        try:
            retry_at = (now + timedelta(seconds=seconds)).isoformat()
        except OverflowError:
            retry_at = datetime.max.replace(tzinfo=timezone.utc).isoformat()
        observe(conn, key, {"retry_at": retry_at, "error": error}, source_url=url, source_type="wire")
        raise
