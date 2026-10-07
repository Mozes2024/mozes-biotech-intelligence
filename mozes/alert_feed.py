"""Small public alert feed; publishing failures never undo alert delivery."""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import db
from .alert_dispatch import configured_channels, push_allowed, recent_alerts

MAX_FEED_BYTES = 256 * 1024
CONFIG_KEY = "alerts:channel_configuration"


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _feed_url():
    url = os.environ.get("MOZES_ALERT_FEED_URL", "").strip()
    try:
        parts = urlsplit(url)
        if (parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
                and parts.port in (None, 443) and not parts.query and not parts.fragment):
            return url
    except ValueError:
        pass
    return None


def remember_channels(conn):
    """Only the push owner records configuration; Pages/deep export cannot turn it off."""
    if not push_allowed():
        return
    value = {"enabled_channels": [c for c in configured_channels() if c != "log"],
             "feed_url": _feed_url(), "checked_at": db.utcnow()}
    with conn:
        conn.execute(
            "INSERT INTO monitor_observations VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(observation_key) DO UPDATE SET value_json=excluded.value_json,"
            "content_hash=excluded.content_hash,observed_at=excluded.observed_at",
            (CONFIG_KEY, _encode(value).decode(), hashlib.sha256(_encode(value)).hexdigest(),
             None, "alert_configuration", value["checked_at"]),
        )


def channel_configuration(conn):
    row = conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key=?",
                       (CONFIG_KEY,)).fetchone()
    return json.loads(row[0]) if row else {"enabled_channels": [], "feed_url": None, "checked_at": None}


def build_feed(conn, *, generated_at=None):
    alerts = recent_alerts(conn)
    config = channel_configuration(conn)
    # The timestamp is freshness metadata, not content: unchanged feeds keep one revision.
    content = {"schema": 1, "alerts": alerts,
               "alert_config": {k: config.get(k) for k in ("enabled_channels", "feed_url")},
               "truncated": False}
    while len(_encode(content)) > MAX_FEED_BYTES - 1024 and content["alerts"]:
        content["alerts"].pop()
        content["truncated"] = True
    return {**content, "revision": hashlib.sha256(_encode(content)).hexdigest(),
            "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")}


def publish_feed(conn, *, opener=None):
    if not push_allowed() or not os.environ.get("MOZES_ALERT_FEED_URL"):
        return {"status": "DISABLED"}
    url, token = _feed_url(), os.environ.get("MOZES_ALERT_FEED_TOKEN")
    if not url or not token:
        return {"status": "MISCONFIGURED", "error": "feed URL/token missing or invalid"}
    try:
        # One serialized hot workflow owns this sequence. Deep jobs never publish here.
        sequence = int(os.environ["GITHUB_RUN_NUMBER"])
        if sequence < 1:
            raise ValueError("invalid run number")
        feed = {**build_feed(conn), "sequence": sequence}
        request = urllib.request.Request(url, data=_encode(feed), method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + token,
            "User-Agent": "MozesAlertFeed/1.0",
        })
        # Do not forward the shared secret to a redirect destination.
        if opener is None:
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            opener = urllib.request.build_opener(NoRedirect()).open
        with opener(request, timeout=10) as response:
            response.read(2048)
            return {"status": "OK", "http_status": response.status, "revision": feed["revision"]}
    except Exception as exc:
        # Exceptions from HTTP libraries can include URLs. Export only the error class.
        return {"status": "FAILED", "error": type(exc).__name__}


def main():
    from .config import DB_PATH
    conn = db.connect(DB_PATH)
    try:
        remember_channels(conn)
        result = publish_feed(conn)
        print(json.dumps(result))
        return 0 if result["status"] in {"OK", "DISABLED"} else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
