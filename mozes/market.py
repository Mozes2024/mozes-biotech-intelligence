"""Calendar-aligned market calculations and session-aware catalyst returns."""
from __future__ import annotations

from bisect import bisect_left
from datetime import date


def _sorted(rows):
    return sorted(rows, key=lambda r: r["date"])


def join_on_dates(stock_rows, benchmark_rows):
    """Inner join by actual trading date; never align by row position."""
    b = {r["date"]: r for r in benchmark_rows}
    return [(s, b[s["date"]]) for s in _sorted(stock_rows) if s["date"] in b]


def prior_close(rows, d: str):
    rs = _sorted(rows)
    dates = [r["date"] for r in rs]
    i = bisect_left(dates, d) - 1
    return rs[i] if i >= 0 else None


def close_on_or_after(rows, d: str):
    rs = _sorted(rows)
    dates = [r["date"] for r in rs]
    i = bisect_left(dates, d)
    return rs[i] if i < len(rs) else None


def next_close(rows, d: str):
    rs = _sorted(rows)
    dates = [r["date"] for r in rs]
    i = bisect_left(dates, d)
    while i < len(rs) and rs[i]["date"] <= d:
        i += 1
    return rs[i] if i < len(rs) else None


def event_return(rows, event_date: str, session: str = "unknown") -> dict:
    """Return a conservative event move using announcement session.

    premarket: prior close -> same-day close
    afterhours: prior close -> next trading-day close
    intraday: prior close -> same-day close
    unknown: prior close -> next trading-day close and timing_uncertain=True
    """
    before = prior_close(rows, event_date)
    if not before:
        return {"available": False, "reason": "no prior close"}
    if session in {"premarket", "intraday"}:
        after = close_on_or_after(rows, event_date)
    else:
        after = next_close(rows, event_date)
    if not after:
        return {"available": False, "reason": "no post-event close"}
    return {
        "available": True,
        "from_date": before["date"],
        "to_date": after["date"],
        "return": after["close"] / before["close"] - 1,
        "session": session,
        "timing_uncertain": session == "unknown",
    }


def excess_return(stock_rows, benchmark_rows, start_date: str, end_date: str):
    pairs = join_on_dates(stock_rows, benchmark_rows)
    if not pairs:
        return None
    d = {s["date"]: (s, b) for s, b in pairs}
    if start_date not in d or end_date not in d:
        return None
    s0, b0 = d[start_date]
    s1, b1 = d[end_date]
    return (s1["close"] / s0["close"] - 1) - (b1["close"] / b0["close"] - 1)
