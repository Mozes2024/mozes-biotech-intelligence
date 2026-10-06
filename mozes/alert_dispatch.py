"""Transactional alert outbox + push adapters (ntfy / webhook / log).

Dashboard/Pages publication is a consumer, never the alert transport.
Telegram/email delivery channels are intentionally deferred.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import db
from .latency_metrics import mark_queued, mark_sent, record_detection
from .materiality import alert_priority, classify_outcome, should_enqueue

STAGE1 = "stage1"
STAGE2 = "stage2"
CHANNELS = ("ntfy", "webhook", "log")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _alert_id(change_id, stage, channel):
    return "ALT-" + hashlib.sha256(_json([change_id, stage, channel]).encode()).hexdigest()[:24]


def _delivery_id(alert_id, attempt):
    return "DLV-" + hashlib.sha256(_json([alert_id, attempt]).encode()).hexdigest()[:24]


def configured_channels():
    channels = []
    if os.environ.get("MOZES_NTFY_URL"):
        channels.append("ntfy")
    if os.environ.get("MOZES_WEBHOOK_URL"):
        channels.append("webhook")
    if os.environ.get("MOZES_ALERT_LOG", "1") == "1" and not channels:
        channels.append("log")
    return tuple(channels) or ("log",)


def build_stage1_payload(conn, change_id):
    row = conn.execute("SELECT * FROM change_events WHERE change_id=?", (change_id,)).fetchone()
    if not row:
        raise KeyError(change_id)
    new_value = json.loads(row["new_value"] or "null")
    meta = json.loads(row["metadata_json"] or "{}")
    text_bits = []
    if isinstance(new_value, dict):
        text_bits.extend(str(new_value.get(k) or "") for k in ("headline", "title", "form", "summary"))
    elif new_value is not None:
        text_bits.append(str(new_value))
    outcome = classify_outcome(" ".join(text_bits))
    priority = alert_priority(row["change_type"], row["severity"], outcome)
    published = None
    if isinstance(new_value, dict):
        published = new_value.get("published_at") or new_value.get("accepted")
    published = published or meta.get("published_at") or meta.get("source_published_at")
    return {
        "stage": STAGE1,
        "priority": priority,
        "ticker": row["ticker"],
        "change_id": change_id,
        "change_type": row["change_type"],
        "severity": row["severity"],
        "verification_state": row["verification_state"],
        "source_type": row["source_type"],
        "source_url": row["source_url"],
        "detected_at": row["detected_at"],
        "published_at": published,
        "new_value": new_value,
        "outcome": outcome,
        "note": "Analytical scores are uncalibrated; not a buy recommendation.",
    }


def enqueue_change(conn, change_id, *, stage=STAGE1, channels=None, send_after=None):
    """Exactly-once enqueue via UNIQUE(change_id, stage, channel)."""
    if stage not in {STAGE1, STAGE2}:
        raise ValueError("unsupported alert stage")
    channels = tuple(channels or configured_channels())
    payload = build_stage1_payload(conn, change_id)
    if stage == STAGE2:
        payload = {**payload, "stage": STAGE2}
    now = db.utcnow()
    send_after = send_after or now
    created = []
    row = conn.execute("SELECT * FROM change_events WHERE change_id=?", (change_id,)).fetchone()
    record_detection(
        conn, change_id=change_id, source_type=row["source_type"] if row else None,
        source_published_at=payload.get("published_at"),
        source_first_seen_at=row["detected_at"] if row else now,
        change_created_at=row["detected_at"] if row else now,
        metadata={"stage": stage},
    )
    for channel in channels:
        alert_id = _alert_id(change_id, stage, channel)
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO alert_outbox("
                "alert_id,change_id,stage,channel,payload_json,status,attempts,created_at,send_after) "
                "VALUES(?,?,?,?,?,'pending',0,?,?)",
                (alert_id, change_id, stage, channel, _json(payload), now, send_after),
            )
        if cur.rowcount:
            mark_queued(conn, change_id=change_id, alert_id=alert_id, queued_at=now)
            created.append(alert_id)
    return created


def enqueue_from_change_row(conn, *, change_id, change_type, severity):
    if not should_enqueue(change_type, severity):
        return []
    return enqueue_change(conn, change_id, stage=STAGE1)


def _format_message(payload):
    outcome = payload.get("outcome") or {}
    headline = ""
    value = payload.get("new_value")
    if isinstance(value, dict):
        headline = value.get("headline") or value.get("title") or value.get("form") or ""
    elif value is not None:
        headline = str(value)[:240]
    return (
        f"[{payload.get('priority')}] {payload.get('ticker') or '?'} · {payload.get('stage')} · "
        f"{payload.get('change_type')}\n"
        f"outcome={outcome.get('polarity', 'unknown')} (uncalibrated)\n"
        f"{headline}\n"
        f"source={payload.get('source_type')} · detected={payload.get('detected_at')}\n"
        f"{payload.get('source_url') or ''}\n"
        f"{payload.get('note')}"
    )


def _post_json(url, body, *, headers=None, timeout=10):
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "MozesBiotechAlert/1.0",
                 **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(64_000).decode("utf-8", errors="replace"), response.status


def send_ntfy(payload):
    url = os.environ["MOZES_NTFY_URL"]
    request = urllib.request.Request(
        url, data=_format_message(payload).encode("utf-8"), method="POST",
        headers={
            "Title": f"{payload.get('priority')} {payload.get('ticker') or ''} {payload.get('change_type')}".strip(),
            "Priority": "5" if payload.get("priority") == "P1" else "3",
            "Tags": "biotech,warning",
            "User-Agent": "MozesBiotechAlert/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read(64_000).decode("utf-8", errors="replace")
        return {"provider_message_id": f"ntfy:{response.status}", "raw": raw[:500]}


def send_webhook(payload):
    url = os.environ["MOZES_WEBHOOK_URL"]
    text, status = _post_json(url, payload)
    return {"provider_message_id": f"webhook:{status}", "raw": text[:500]}


def send_log(payload):
    return {"provider_message_id": "log:ok", "raw": _format_message(payload)[:500]}


ADAPTERS = {
    "ntfy": send_ntfy,
    "webhook": send_webhook,
    "log": send_log,
}


def _backoff_seconds(attempts):
    return min(3600, 30 * (2 ** max(0, attempts - 1)))


def dispatch_pending(conn, *, limit=50, now=None, sender=None):
    """Claim pending rows, send once, retry with exponential backoff, dead-letter after 8 attempts."""
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    rows = conn.execute(
        "SELECT * FROM alert_outbox WHERE status IN ('pending','failed') AND send_after<=? "
        "ORDER BY created_at ASC LIMIT ?",
        (now_iso, limit),
    ).fetchall()
    results = {"sent": 0, "failed": 0, "dead": 0, "errors": []}
    for row in rows:
        alert_id = row["alert_id"]
        with conn:
            claimed = conn.execute(
                "UPDATE alert_outbox SET status='sending', attempts=attempts+1 "
                "WHERE alert_id=? AND status IN ('pending','failed')",
                (alert_id,),
            )
        if claimed.rowcount != 1:
            continue
        payload = json.loads(row["payload_json"])
        channel = row["channel"]
        adapter = (sender or ADAPTERS).get(channel)
        attempt = int(row["attempts"]) + 1
        try:
            if not adapter:
                raise RuntimeError(f"no adapter for channel {channel}")
            receipt = adapter(payload)
            delivery_id = _delivery_id(alert_id, attempt)
            with conn:
                conn.execute(
                    "INSERT INTO alert_deliveries(delivery_id,alert_id,provider_message_id,sent_at,ack_at,error_code,retry_count,metadata_json) "
                    "VALUES(?,?,?,?,NULL,NULL,?,?)",
                    (delivery_id, alert_id, receipt.get("provider_message_id"), now_iso, attempt,
                     _json({"raw": receipt.get("raw")})),
                )
                conn.execute(
                    "UPDATE alert_outbox SET status='sent', sent_at=?, last_error=NULL WHERE alert_id=?",
                    (now_iso, alert_id),
                )
            mark_sent(conn, alert_id=alert_id, sent_at=now_iso)
            results["sent"] += 1
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, RuntimeError, KeyError, ValueError) as exc:
            error = str(exc)[:240]
            if attempt >= 8:
                status, results["dead"] = "dead", results["dead"] + 1
                send_after = now_iso
            else:
                status, results["failed"] = "failed", results["failed"] + 1
                send_after = (now + timedelta(seconds=_backoff_seconds(attempt))).isoformat()
            with conn:
                conn.execute(
                    "UPDATE alert_outbox SET status=?, last_error=?, send_after=? WHERE alert_id=?",
                    (status, error, send_after, alert_id),
                )
            results["errors"].append({"alert_id": alert_id, "error": error})
    return results


def sync_new_changes(conn, *, since_iso=None, limit=100):
    """Enqueue Stage-1 alerts for recent alertable change_events, then dispatch."""
    if since_iso:
        rows = conn.execute(
            "SELECT change_id,change_type,severity FROM change_events "
            "WHERE detected_at>=? ORDER BY detected_at ASC LIMIT ?",
            (since_iso, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.change_id,c.change_type,c.severity FROM change_events c "
            "LEFT JOIN alert_outbox o ON o.change_id=c.change_id AND o.stage=? "
            "WHERE o.alert_id IS NULL ORDER BY c.detected_at DESC LIMIT ?",
            (STAGE1, limit),
        ).fetchall()
    queued = []
    for row in rows:
        queued.extend(enqueue_from_change_row(
            conn, change_id=row["change_id"], change_type=row["change_type"], severity=row["severity"]))
    dispatched = dispatch_pending(conn)
    return {"queued": len(queued), "alert_ids": queued, "dispatch": dispatched}
