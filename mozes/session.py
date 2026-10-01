"""Announcement-session classification for U.S. market event studies."""
from __future__ import annotations

import re
from datetime import datetime, date, timedelta, timezone


def new_york_time(value):
    """Modern (2007+) US DST rules, without depending on OS zoneinfo data."""
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.year < 2007:
        raise ValueError('explicit timezone and modern timestamp required')
    stamp = stamp.astimezone(timezone.utc)
    march, november = date(stamp.year, 3, 1), date(stamp.year, 11, 1)
    start = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    lo = datetime.combine(start, datetime.min.time(), timezone.utc) + timedelta(hours=7)
    hi = datetime.combine(end, datetime.min.time(), timezone.utc) + timedelta(hours=6)
    return stamp.astimezone(timezone(timedelta(hours=-4 if lo <= stamp < hi else -5)))


def session_from_publication(value):
    """Actual issuer/wire release timestamp only; dates/naive clocks are unknown."""
    try:
        stamp = new_york_time(value)
    except (ValueError, TypeError, AttributeError):
        return 'unknown'
    if stamp.weekday() >= 5:
        return 'unknown'
    minutes = stamp.hour * 60 + stamp.minute
    return 'premarket' if minutes < 570 else 'afterhours' if minutes >= 960 else 'intraday'


def session_from_sec_acceptance(value: str | None) -> str:
    """Classify SEC acceptance datetime by clock time.

    EDGAR acceptance timestamps are used as a practical proxy when no press-release
    timestamp is available. They are not assumed to equal the exact news-release time.
    """
    if not value:
        return "unknown"
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 12:
        return "unknown"
    # YYYYMMDDHHMMSS (or a prefix with at least YYYYMMDDHHMM)
    hh, mm = int(digits[8:10]), int(digits[10:12])
    mins = hh * 60 + mm
    if mins < 9 * 60 + 30:
        return "premarket"
    if mins >= 16 * 60:
        return "afterhours"
    return "intraday"
