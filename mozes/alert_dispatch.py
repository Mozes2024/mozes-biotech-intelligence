"""Transactional alert outbox + push adapters (ntfy / email / webhook / log).

Dashboard/Pages publication is a consumer, never the alert transport.
Telegram delivery is intentionally deferred.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone

from . import db
from .latency_metrics import mark_queued, mark_sent, record_detection
from .materiality import ALERTABLE_CHANGE_TYPES, PRIORITY_RANK, alert_priority, classify_outcome, should_enqueue
from .priority import priority_tickers

STAGE1 = "stage1"
STAGE2 = "stage2"
CHANNELS = ("ntfy", "email", "webhook", "log")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _alert_id(change_id, stage, channel):
    return "ALT-" + hashlib.sha256(_json([change_id, stage, channel]).encode()).hexdigest()[:24]


def _delivery_id(alert_id, attempt):
    return "DLV-" + hashlib.sha256(_json([alert_id, attempt]).encode()).hexdigest()[:24]


def push_allowed():
    """Exactly one lineage may push; on Actions that is the serialized hot workflow, opted in explicitly."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return os.environ.get("MOZES_PUSH_FROM_ACTIONS") == "1"
    return True


def configured_channels():
    channels = []
    if push_allowed() and os.environ.get("MOZES_NTFY_URL"):
        channels.append("ntfy")
    if push_allowed() and os.environ.get("MOZES_SMTP_USER") and os.environ.get("MOZES_SMTP_PASSWORD"):
        channels.append("email")
    if push_allowed() and os.environ.get("MOZES_WEBHOOK_URL"):
        channels.append("webhook")
    if os.environ.get("MOZES_ALERT_LOG", "1") == "1" and not channels:
        channels.append("log")
    return tuple(channels) or ("log",)


def is_watched(conn, ticker):
    if not ticker:
        return False
    ticker = str(ticker).upper()
    if ticker in set(priority_tickers()):
        return True
    return conn.execute("SELECT 1 FROM watch_universe WHERE ticker=? AND active=1", (ticker,)).fetchone() is not None


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
    watched = is_watched(conn, row["ticker"])
    priority = alert_priority(row["change_type"], row["severity"], outcome,
                              ticker=row["ticker"], watched=watched)
    published = None
    if isinstance(new_value, dict):
        published = new_value.get("published_at") or new_value.get("accepted")
    published = published or meta.get("published_at") or meta.get("source_published_at")
    return {
        "stage": STAGE1,
        "priority": priority,
        "ticker": row["ticker"],
        "watched": watched,
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


CONSOLIDATION_WINDOW_MINUTES = 30
_STOPWORDS = frozenset(
    "the and for with from that this its into over announces announce announced reports report reported "
    "inc ltd corp plc company therapeutics pharmaceuticals pharma biotherapeutics biosciences "
    "results data trial study phase patients".split())


def _headline_of(payload):
    value = payload.get("new_value")
    if isinstance(value, dict):
        return str(value.get("headline") or value.get("title") or "")
    return ""


def _tokens(text):
    return {word for word in re.findall(r"[a-z0-9][a-z0-9-]{2,}", (text or "").lower()) if word not in _STOPWORDS}


def headline_similarity(left, right):
    a, b = _tokens(left), _tokens(right)
    jaccard = len(a & b) / len(a | b) if a and b else 0.0
    ratio = difflib.SequenceMatcher(None, (left or "").lower(), (right or "").lower()).ratio()
    return max(jaccard, ratio)


def find_primary_alert(conn, payload, *, window_minutes=CONSOLIDATION_WINDOW_MINUTES, threshold=0.55):
    """Earlier Stage-1 alert for the same ticker and story within the window, if any."""
    ticker, headline = payload.get("ticker"), _headline_of(payload)
    if not ticker or not headline:
        return None
    try:
        detected = datetime.fromisoformat(str(payload.get("detected_at")))
    except ValueError:
        detected = datetime.now(timezone.utc)
    since = (detected - timedelta(minutes=window_minutes)).isoformat()
    rows = conn.execute(
        "SELECT change_id, payload_json FROM alert_outbox WHERE stage=? AND change_id<>? AND created_at>=? "
        "ORDER BY created_at ASC",
        (STAGE1, payload["change_id"], since),
    ).fetchall()
    for row in rows:
        other = json.loads(row["payload_json"])
        if other.get("ticker") != ticker:
            continue
        similarity = headline_similarity(headline, _headline_of(other))
        if similarity >= threshold:
            return {"change_id": row["change_id"], "similarity": round(similarity, 3)}
    return None


def link_corroboration(conn, *, change_id, primary_change_id, ticker, similarity, source_type):
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO alert_links(change_id,primary_change_id,ticker,similarity,source_type,linked_at) "
            "VALUES(?,?,?,?,?,?)",
            (change_id, primary_change_id, ticker, similarity, source_type, db.utcnow()),
        )


def corroborations(conn, primary_change_id):
    return [dict(row) for row in conn.execute(
        "SELECT * FROM alert_links WHERE primary_change_id=? ORDER BY linked_at", (primary_change_id,))]


def recent_alerts(conn, *, days=7, limit=100, now=None):
    """One row per Stage-1 alert for the site, newest first, regardless of push channel or delivery."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT change_id, MIN(created_at) AS created_at, payload_json, "
        "MAX(CASE WHEN status='sent' AND channel<>'log' THEN 1 ELSE 0 END) AS delivered "
        "FROM alert_outbox WHERE stage=? AND created_at>=? GROUP BY change_id "
        "ORDER BY created_at DESC LIMIT ?",
        (STAGE1, since, limit),
    ).fetchall()
    alerts = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        links = corroborations(conn, row["change_id"])
        delivery = {item["channel"]: {"status": item["status"], "attempts": item["attempts"],
                                     "sent_at": item["sent_at"]}
                    for item in conn.execute(
                        "SELECT channel,status,attempts,sent_at FROM alert_outbox WHERE change_id=? AND stage=?",
                        (row["change_id"], STAGE1))}
        analysis = conn.execute(
            "SELECT a.result_json FROM alert_analysis_jobs j JOIN alert_analyses a USING(analysis_id) WHERE j.change_id=?",
            (row["change_id"],)).fetchone()
        alerts.append({
            "change_id": row["change_id"],
            "created_at": row["created_at"],
            "priority": payload.get("priority"),
            "ticker": payload.get("ticker"),
            "watched": payload.get("watched"),
            "change_type": payload.get("change_type"),
            "source_type": payload.get("source_type"),
            "source_url": payload.get("source_url"),
            "published_at": payload.get("published_at"),
            "detected_at": payload.get("detected_at"),
            "headline": _headline_of(payload)[:1200] or None,
            "polarity": (payload.get("outcome") or {}).get("polarity", "unknown"),
            "verification_state": payload.get("verification_state"),
            "summary": str((payload.get("new_value") or {}).get("summary") or "")[:1200]
                       if isinstance(payload.get("new_value"), dict) else "",
            "corroborated_by": [link["source_type"] for link in links],
            "delivered": bool(row["delivered"]),
            "delivery": delivery,
            "explanation": json.loads(analysis[0]) if analysis else None,
        })
    return alerts


def _min_priority():
    value = os.environ.get("MOZES_ALERT_MIN_PRIORITY", "P2").upper()
    return PRIORITY_RANK.get(value, 2)


def enqueue_from_change_row(conn, *, change_id, change_type, severity):
    """Gate: alertable type, priority at or above MOZES_ALERT_MIN_PRIORITY, not a duplicate story."""
    if not should_enqueue(change_type, severity):
        _enqueue_decision(conn, change_id, "filtered")
        return []
    payload = build_stage1_payload(conn, change_id)
    if PRIORITY_RANK.get(payload["priority"], 3) > _min_priority():
        _enqueue_decision(conn, change_id, "filtered")
        return []
    primary = find_primary_alert(conn, payload)
    if primary:
        link_corroboration(conn, change_id=change_id, primary_change_id=primary["change_id"],
                           ticker=payload["ticker"], similarity=primary["similarity"],
                            source_type=payload.get("source_type"))
        _enqueue_decision(conn, change_id, "linked")
        return []
    created = enqueue_change(conn, change_id, stage=STAGE1)
    _enqueue_decision(conn, change_id, "queued")
    return created


def _enqueue_policy():
    return f"enqueue-v1:P{_min_priority()}"


def _enqueue_decision(conn, change_id, decision):
    with conn:
        conn.execute("INSERT OR REPLACE INTO alert_enqueue_decisions VALUES(?,?,?,?)",
                     (change_id, _enqueue_policy(), decision, db.utcnow()))


def _format_message(payload):
    from .alert_explanation import explain
    explanation = explain(payload)
    headline = ""
    value = payload.get("new_value")
    if isinstance(value, dict):
        headline = value.get("headline") or value.get("title") or value.get("form") or ""
    elif value is not None:
        headline = str(value)[:240]
    return (
        f"[{payload.get('priority')}] {payload.get('ticker') or '?'} · {payload.get('stage')} · "
        f"{payload.get('change_type')}\n"
        f"{headline}\n"
        f"למה התקבלה ההתראה? {explanation['why_he']}\n"
        f"{explanation['summary_he']}\n"
        f"רמת המידע: כותרת/אות ראשוני; ניתוח המקור יתווסף באתר.\n"
        f"מקור: {payload.get('source_type')} · זוהה: {payload.get('detected_at')}\n"
        f"{payload.get('source_url') or ''}\n"
        f"התראות וניתוח: https://mozes2024.github.io/mozes-biotech-intelligence/#alerts\n"
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


def _email_subject(payload):
    return " ".join(str(part) for part in (
        "MOZES", payload.get("priority"), payload.get("ticker") or "", payload.get("change_type"),
    ) if part)


def send_email(payload, *, smtp_factory=None):
    """SMTP defaults to Gmail (App Password); the recipient defaults to the sending account."""
    user = os.environ["MOZES_SMTP_USER"]
    host = os.environ.get("MOZES_SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("MOZES_SMTP_PORT") or 465)
    message = EmailMessage()
    message["Subject"] = _email_subject(payload)
    message["From"] = os.environ.get("MOZES_ALERT_EMAIL_FROM") or user
    message["To"] = os.environ.get("MOZES_ALERT_EMAIL_TO") or user
    if payload.get("change_id"):
        message["Message-ID"] = f"<{_alert_id(payload['change_id'], payload.get('stage', STAGE1), 'email')}@mozes-alerts>"
    if payload.get("priority") == "P1":
        message["X-Priority"] = "1"
    message.set_content(_format_message(payload))
    context = ssl.create_default_context()
    if smtp_factory is None:
        smtp_factory = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    kwargs = {"context": context} if smtp_factory is smtplib.SMTP_SSL else {}
    with smtp_factory(host, port, timeout=20, **kwargs) as server:
        if port != 465:
            server.starttls(context=context)
        server.login(user, os.environ["MOZES_SMTP_PASSWORD"])
        server.send_message(message)
    return {"provider_message_id": f"email:{message['To']}", "raw": message["Subject"]}


def send_webhook(payload):
    url = os.environ["MOZES_WEBHOOK_URL"]
    text, status = _post_json(url, payload)
    return {"provider_message_id": f"webhook:{status}", "raw": text[:500]}


def send_log(payload):
    return {"provider_message_id": "log:ok", "raw": _format_message(payload)[:500]}


ADAPTERS = {
    "ntfy": send_ntfy,
    "email": send_email,
    "webhook": send_webhook,
    "log": send_log,
}


def _backoff_seconds(attempts):
    return min(3600, 30 * (2 ** max(0, attempts - 1)))


MAX_ATTEMPTS = 8
SENDING_LEASE_SECONDS = 120


def recover_stale_sending(conn, *, now=None):
    """Release rows whose sender died mid-flight; the lease expiry lives in send_after."""
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    with conn:
        dead = conn.execute(
            "UPDATE alert_outbox SET status='dead', last_error='sending lease expired' "
            "WHERE status='sending' AND send_after<=? AND attempts>=?",
            (now_iso, MAX_ATTEMPTS),
        ).rowcount
        retried = conn.execute(
            "UPDATE alert_outbox SET status='failed', last_error='sending lease expired' "
            "WHERE status='sending' AND send_after<=?",
            (now_iso,),
        ).rowcount
    return {"retried": retried, "dead": dead}


def dispatch_pending(conn, *, limit=50, now=None, sender=None):
    """Claim pending rows, send once, retry with exponential backoff, dead-letter after 8 attempts.

    Delivery is at-least-once: a crash after the provider accepted but before the row is
    marked sent will resend once the lease expires.
    """
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    recovered = recover_stale_sending(conn, now=now)
    lease_until = (now + timedelta(seconds=SENDING_LEASE_SECONDS)).isoformat()
    rows = conn.execute(
        "SELECT * FROM alert_outbox WHERE status IN ('pending','failed') AND send_after<=? "
        "ORDER BY created_at ASC LIMIT ?",
        (now_iso, limit),
    ).fetchall()
    results = {"sent": 0, "failed": 0, "dead": 0, "errors": [], "recovered": recovered}
    for row in rows:
        alert_id = row["alert_id"]
        with conn:
            claimed = conn.execute(
                "UPDATE alert_outbox SET status='sending', attempts=attempts+1, send_after=? "
                "WHERE alert_id=? AND status IN ('pending','failed')",
                (lease_until, alert_id),
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
        except Exception as exc:  # noqa: BLE001 - any adapter failure is a retryable delivery error
            error = f"{type(exc).__name__}: {exc}"[:240]
            if attempt >= MAX_ATTEMPTS:
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
    """Recover missing Stage-1 entries from the last seven days, then dispatch.

    Current-pass detections go first. Recorded filters are not rescanned every pass;
    a transient enqueue failure remains eligible even after its original pass ended.
    """
    from .alert_feed import remember_channels
    remember_channels(conn)
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    types = sorted(ALERTABLE_CHANGE_TYPES)
    rows = conn.execute(
        "SELECT c.change_id,c.change_type,c.severity FROM change_events c "
        "WHERE c.detected_at>=? AND c.change_type IN (" + ",".join("?" for _ in types) + ") "
        "AND c.severity IN ('medium','high','critical') "
        "AND NOT EXISTS (SELECT 1 FROM alert_outbox o WHERE o.change_id=c.change_id AND o.stage=?) "
        "AND NOT EXISTS (SELECT 1 FROM alert_links l WHERE l.change_id=c.change_id) "
        "AND NOT EXISTS (SELECT 1 FROM alert_enqueue_decisions d WHERE d.change_id=c.change_id AND d.policy_key=?) "
        "ORDER BY CASE WHEN c.detected_at>=? THEN 0 ELSE 1 END,c.detected_at ASC LIMIT ?",
        (since, *types, STAGE1, _enqueue_policy(), since_iso or since, max(1, limit)),
    ).fetchall()
    queued, errors = [], []
    for row in rows:
        try:
            queued.extend(enqueue_from_change_row(
                conn, change_id=row["change_id"], change_type=row["change_type"], severity=row["severity"]))
        except Exception as exc:
            errors.append({"change_id": row["change_id"], "error": type(exc).__name__})
    dispatched = dispatch_pending(conn)
    return {"queued": len(queued), "alert_ids": queued, "dispatch": dispatched, "errors": errors}
