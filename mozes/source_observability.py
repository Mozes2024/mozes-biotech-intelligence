"""Request-level source-operation metrics for persisted refresh metadata."""
from __future__ import annotations

import contextvars
import time
from functools import wraps
from datetime import datetime, timezone
from contextlib import contextmanager

_METRICS = contextvars.ContextVar("mozes_source_metrics", default=None)


class Metrics:
    def __init__(self):
        self.started = time.monotonic()
        self.sources = {}

    def record(self, source, *, cache=None, error=False, duration_ms=0):
        row = self.sources.setdefault(source, {"requests": 0, "errors": 0, "cache_hits": 0, "cache_misses": 0, "duration_ms": 0})
        row["requests"] += 1
        row["errors"] += int(error)
        if cache is True: row["cache_hits"] += 1
        if cache is False: row["cache_misses"] += 1
        row["duration_ms"] += round(duration_ms)
        now = datetime.now(timezone.utc).isoformat()
        row["success_count"] = row["requests"] - row["errors"]
        row["last_error" if error else "last_success"] = now
        row["state"] = "PARTIAL" if row["errors"] and row["success_count"] else "FAILED" if row["errors"] else "OK"

    def snapshot(self):
        return {"duration_ms": round((time.monotonic() - self.started) * 1000), "sources": self.sources}


@contextmanager
def capture():
    metrics = Metrics()
    token = _METRICS.set(metrics)
    try:
        yield metrics
    finally:
        _METRICS.reset(token)


def record(source, **kwargs):
    metrics = _METRICS.get()
    if metrics:
        metrics.record(source, **kwargs)


def snapshot():
    metrics = _METRICS.get()
    return metrics.snapshot() if metrics else None


def observed(function):
    """Capture one operation without leaking counters across concurrent requests."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with capture():
            return function(*args, **kwargs)
    return wrapped
