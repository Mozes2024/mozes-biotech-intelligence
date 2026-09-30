"""Announcement-session classification for U.S. market event studies."""
from __future__ import annotations

import re


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
