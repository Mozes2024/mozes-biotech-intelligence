"""End-to-end latency timestamps: publication → detection → outbox → delivery."""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone

from . import db


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _id(*parts):
    return "LAT-" + hashlib.sha256(_json(parts).encode()).hexdigest()[:24]


def record_detection(conn, *, change_id, source_type, source_published_at=None,
                     source_first_seen_at=None, change_created_at=None, metadata=None):
    seen = source_first_seen_at or db.utcnow()
    created = change_created_at or seen
    latency_id = _id(change_id, "detection")
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO latency_events("
            "latency_id,change_id,alert_id,source_type,source_published_at,"
            "source_first_seen_at,change_created_at,alert_queued_at,alert_sent_at,metadata_json) "
            "VALUES(?,?,NULL,?,?,?,?,NULL,NULL,?)",
            (latency_id, change_id, source_type, source_published_at, seen, created,
             _json(metadata or {})),
        )
    return latency_id


def mark_queued(conn, *, change_id, alert_id, queued_at=None):
    queued_at = queued_at or db.utcnow()
    with conn:
        conn.execute(
            "UPDATE latency_events SET alert_id=?, alert_queued_at=COALESCE(alert_queued_at, ?) "
            "WHERE change_id=?",
            (alert_id, queued_at, change_id),
        )


def mark_sent(conn, *, change_id=None, alert_id=None, sent_at=None):
    sent_at = sent_at or db.utcnow()
    with conn:
        if alert_id:
            conn.execute(
                "UPDATE latency_events SET alert_sent_at=COALESCE(alert_sent_at, ?) WHERE alert_id=?",
                (sent_at, alert_id),
            )
        elif change_id:
            conn.execute(
                "UPDATE latency_events SET alert_sent_at=COALESCE(alert_sent_at, ?) WHERE change_id=?",
                (sent_at, change_id),
            )


def _seconds(start, end):
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return max(0.0, (b - a).total_seconds())


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return round(ordered[idx], 3)


def summary(conn, *, limit=500):
    rows = conn.execute(
        "SELECT * FROM latency_events ORDER BY source_first_seen_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    detection, dispatch = [], []
    by_source = {}
    for row in rows:
        det = _seconds(row["source_published_at"], row["source_first_seen_at"])
        if det is not None:
            detection.append(det)
            by_source.setdefault(row["source_type"] or "unknown", []).append(det)
        disp = _seconds(row["alert_queued_at"], row["alert_sent_at"])
        if disp is not None:
            dispatch.append(disp)
    return {
        "samples": len(rows),
        "detection_latency_seconds": {
            "p50": _percentile(detection, 0.50),
            "p95": _percentile(detection, 0.95),
            "p99": _percentile(detection, 0.99),
            "mean": round(statistics.fmean(detection), 3) if detection else None,
            "n": len(detection),
        },
        "dispatch_latency_seconds": {
            "p95": _percentile(dispatch, 0.95),
            "n": len(dispatch),
        },
        "by_source_type": {
            source: {"p95": _percentile(vals, 0.95), "n": len(vals)}
            for source, vals in sorted(by_source.items())
        },
    }
