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
    afterhours: event-day close (before announcement) -> next trading-day close
    intraday: prior close -> same-day close
    unknown: prior close -> next trading-day close and timing_uncertain=True
    """
    before = prior_close(rows, event_date)
    if session == "afterhours":
        before = next((row for row in _sorted(rows) if row["date"] == event_date), before)
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


def pre_event_prices(rows, event_date, session):
    if session not in {"premarket", "intraday", "afterhours"}:
        return []
    return [row for row in _sorted(rows) if row["date"] < event_date or
            (session == "afterhours" and row["date"] == event_date)]


def abnormal_return(stock, benchmark, event_date, session, min_estimation=30):
    """Daily descriptive market model; beta estimated strictly before the event window."""
    move = event_return(stock, event_date, session)
    if session == "unknown" or not move.get("available"):
        return {"available": False, "reason": "session_or_prices_unavailable"}
    pairs = join_on_dates(stock, benchmark)
    estimation = [(s, b) for s, b in pairs if s["date"] < move["from_date"]][-121:]
    returns = [(estimation[i][0]["close"] / estimation[i-1][0]["close"] - 1,
                estimation[i][1]["close"] / estimation[i-1][1]["close"] - 1) for i in range(1, len(estimation))]
    simple = excess_return(stock, benchmark, move["from_date"], move["to_date"])
    out = {"available": True, "stock_return": move["return"], "xbi_relative_return": simple,
           "beta": None, "car": None, "estimation_n": len(returns), "research_only": True}
    if len(returns) < min_estimation:
        return out
    mean_s = sum(s for s, b in returns) / len(returns)
    mean_b = sum(b for s, b in returns) / len(returns)
    variance = sum((b - mean_b) ** 2 for s, b in returns)
    if variance <= 1e-15:
        return out
    beta = sum((s - mean_s) * (b - mean_b) for s, b in returns) / variance
    alpha = mean_s - beta * mean_b
    period = [(s, b) for s, b in pairs if move["from_date"] <= s["date"] <= move["to_date"]]
    if len(period) < 2 or period[0][0]["date"] != move["from_date"] or period[-1][0]["date"] != move["to_date"]:
        return out
    car = sum((period[i][0]["close"] / period[i-1][0]["close"] - 1) - alpha -
              beta * (period[i][1]["close"] / period[i-1][1]["close"] - 1) for i in range(1, len(period)))
    return {**out, "beta": beta, "car": car}


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
