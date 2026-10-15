"""Calendar-aligned market calculations and session-aware catalyst returns."""
from __future__ import annotations

from bisect import bisect_left
from datetime import date
import math

from .price_validation import validated_prices


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
    try:
        rows = validated_prices(rows)
        if len({row['date'] for row in rows}) != len(rows):
            return {'available': False, 'reason': 'duplicate market dates'}
    except ValueError:
        return {'available': False, 'reason': 'invalid market prices'}
    if session not in {'premarket', 'intraday', 'afterhours', 'unknown'}:
        return {'available': False, 'reason': 'unsupported session'}
    before = prior_close(rows, event_date)
    if session == "afterhours":
        before = next((row for row in _sorted(rows) if row["date"] == event_date), None)
    if not before:
        return {"available": False, "reason": "no prior close"}
    if session in {"premarket", "intraday"}:
        after = next((row for row in rows if row['date'] == event_date), None)
    else:
        after = next_close(rows, event_date)
    if not after:
        return {"available": False, "reason": "no post-event close"}
    gross = after['close'] / before['close'] - 1
    if not math.isfinite(gross) or gross <= -1:
        return {'available': False, 'reason': 'invalid event return'}
    return {
        "available": True,
        "from_date": before["date"],
        "to_date": after["date"],
        "return": gross,
        "session": session,
        "timing_uncertain": session == "unknown",
        "performance_kind": "descriptive_event_study", "executable": False,
    }


def pre_event_prices(rows, event_date, session):
    if session not in {"premarket", "intraday", "afterhours"}:
        return []
    return [row for row in _sorted(rows) if row["date"] < event_date or
            (session == "afterhours" and row["date"] == event_date)]


def _checked_series(rows):
    prices = validated_prices(rows)
    if len({row['date'] for row in prices}) != len(prices):
        raise ValueError('duplicate market dates')
    return prices


def abnormal_return(stock, benchmark, event_date, session, min_estimation=30):
    """Daily descriptive market model; beta estimated strictly before the event window."""
    move = event_return(stock, event_date, session)
    if session == "unknown" or not move.get("available"):
        return {"available": False, "reason": "session_or_prices_unavailable"}
    unavailable = {'available': False, 'stock_return': move['return'],
                   'xbi_relative_return': None, 'beta': None, 'car': None,
                   'estimation_n': None, 'research_only': True}
    try:
        # event_return normalizes a copy; never resume arithmetic on raw strings
        # or silently let a duplicate benchmark date overwrite another record.
        stock, benchmark = _checked_series(stock), _checked_series(benchmark)
    except ValueError:
        return {**unavailable, 'reason': 'invalid_market_model_prices'}
    if not benchmark:
        return {**unavailable, 'reason': 'benchmark_prices_unavailable'}
    pairs = join_on_dates(stock, benchmark)
    estimation = [(s, b) for s, b in pairs if s["date"] < move["from_date"]][-121:]
    returns = [(estimation[i][0]["close"] / estimation[i-1][0]["close"] - 1,
                estimation[i][1]["close"] / estimation[i-1][1]["close"] - 1) for i in range(1, len(estimation))]
    if any(not math.isfinite(value) or value <= -1 for pair in returns for value in pair):
        return {**unavailable, 'reason': 'nonfinite_market_model'}
    simple = excess_return(stock, benchmark, move["from_date"], move["to_date"])
    out = {"available": True, "stock_return": move["return"], "xbi_relative_return": simple,
           "beta": None, "car": None, "estimation_n": len(returns), "research_only": True}
    if not returns or len(returns) < min_estimation:
        return out
    mean_s = sum(s for s, b in returns) / len(returns)
    mean_b = sum(b for s, b in returns) / len(returns)
    if not math.isfinite(mean_s) or not math.isfinite(mean_b):
        return {**unavailable, 'reason': 'nonfinite_market_model'}
    try:
        variance = sum((b - mean_b) ** 2 for s, b in returns)
    except OverflowError:
        return {**unavailable, 'reason': 'nonfinite_market_model'}
    if not math.isfinite(variance):
        return {**unavailable, 'reason': 'nonfinite_market_model'}
    if variance <= 1e-15:
        return out
    beta = sum((s - mean_s) * (b - mean_b) for s, b in returns) / variance
    alpha = mean_s - beta * mean_b
    if not math.isfinite(beta) or not math.isfinite(alpha):
        return {**unavailable, 'reason': 'nonfinite_market_model'}
    period = [(s, b) for s, b in pairs if move["from_date"] <= s["date"] <= move["to_date"]]
    if len(period) < 2 or period[0][0]["date"] != move["from_date"] or period[-1][0]["date"] != move["to_date"]:
        return out
    car = sum((period[i][0]["close"] / period[i-1][0]["close"] - 1) - alpha -
              beta * (period[i][1]["close"] / period[i-1][1]["close"] - 1) for i in range(1, len(period)))
    if not math.isfinite(car):
        return {**unavailable, 'reason': 'nonfinite_market_model'}
    return {**out, "beta": beta, "car": car}


def excess_return(stock_rows, benchmark_rows, start_date: str, end_date: str):
    try:
        stock_rows, benchmark_rows = _checked_series(stock_rows), _checked_series(benchmark_rows)
    except ValueError:
        return None
    pairs = join_on_dates(stock_rows, benchmark_rows)
    if not pairs:
        return None
    d = {s["date"]: (s, b) for s, b in pairs}
    if start_date not in d or end_date not in d:
        return None
    s0, b0 = d[start_date]
    s1, b1 = d[end_date]
    stock_return = s1['close'] / s0['close'] - 1
    benchmark_return = b1['close'] / b0['close'] - 1
    result = stock_return - benchmark_return
    if (not all(math.isfinite(value) for value in (stock_return, benchmark_return, result))
            or stock_return <= -1 or benchmark_return <= -1):
        return None
    return result
