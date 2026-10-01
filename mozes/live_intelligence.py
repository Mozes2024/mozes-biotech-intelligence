"""Lightweight market context and catalyst-chain enrichment for live biotech events.

This module is deliberately observational. It does not change clinical/regulatory evidence
scores, recommendations, or empirical validation gates. Its job is to answer two practical
questions for the UI:

1. Has the stock already made an unusually large move?
2. Is the current row a new/future catalyst, or is the user looking at the catalyst that
   already happened?
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import median

from . import db
from .market import join_on_dates


def _ret(a, b):
    if not a or not b or not a.get("close") or not b.get("close"):
        return None
    return b["close"] / a["close"] - 1


def _label_attention(score: int) -> str:
    if score >= 75:
        return "חריגה מאוד"
    if score >= 50:
        return "גבוהה"
    if score >= 25:
        return "בינונית"
    return "רגילה"


def _market_context_label(code: str) -> str:
    return {
        "stock_specific": "כנראה ספציפית למניה",
        "sector_supported": "גם הסקטור נע באותו כיוון",
        "sector_aligned": "דומה יחסית לתנועת הסקטור",
        "mixed": "הקשר מעורב",
        "unknown": "אין מספיק נתונים",
    }.get(code, code)


def market_intelligence_for_row(conn, row: dict, as_of: str, benchmark: str = "XBI") -> dict:
    """Build descriptive market-attention context from already ingested daily prices.

    Thresholds are transparent heuristics for attention/noise filtering only. They are not
    alpha estimates and do not alter the investment/research recommendation.
    """
    ticker = row.get("ticker")
    if not ticker:
        return {"available": False, "reason": "missing ticker"}

    stock = db.load_prices(conn, ticker, before=as_of)
    bench = db.load_prices(conn, benchmark, before=as_of)
    if len(stock) < 2:
        return {"available": False, "reason": "fewer than 2 closes", "ticker": ticker}

    last, prev = stock[-1], stock[-2]
    one_day = _ret(prev, last)
    prior20 = stock[max(0, len(stock) - 21):-1]
    vols = [float(x["volume"]) for x in prior20 if x.get("volume") not in (None, 0)]
    med_vol = median(vols) if vols else None
    rel_volume = (float(last.get("volume")) / med_vol) if med_vol and last.get("volume") else None
    prior_high = max((float(x["close"]) for x in prior20), default=None)
    breakout_20d = bool(prior_high is not None and float(last["close"]) >= prior_high)
    distance_from_20d_high = (float(last["close"]) / prior_high - 1) if prior_high else None

    market = row.get("market") or {}
    r7 = (market.get("returns") or {}).get("T-7")
    r30 = (market.get("returns") or {}).get("T-30")
    rel7 = (market.get("relative_to_xbi") or {}).get("T-7")
    rel30 = (market.get("relative_to_xbi") or {}).get("T-30")

    pairs = join_on_dates(stock, bench)
    bench_day = None
    if len(pairs) >= 2:
        (_, b0), (_, b1) = pairs[-2], pairs[-1]
        bench_day = _ret(b0, b1)

    context = "unknown"
    if one_day is not None:
        same_direction = bench_day is not None and one_day * bench_day > 0
        if abs(one_day) >= 0.03 and (bench_day is None or abs(bench_day) < 0.015):
            context = "stock_specific"
        elif abs(one_day) >= 0.03 and same_direction and abs(bench_day or 0) >= 0.015:
            context = "sector_supported"
        elif rel7 is not None and abs(rel7) >= 0.10:
            context = "stock_specific"
        elif rel7 is not None and abs(rel7) <= 0.03:
            context = "sector_aligned"
        else:
            context = "mixed"

    score = 0
    if one_day is not None:
        a = abs(one_day)
        score += 30 if a >= 0.10 else 20 if a >= 0.05 else 10 if a >= 0.03 else 0
    if rel_volume is not None:
        score += 25 if rel_volume >= 3 else 18 if rel_volume >= 2 else 8 if rel_volume >= 1.5 else 0
    if breakout_20d:
        score += 10
    if rel7 is not None:
        score += 25 if abs(rel7) >= 0.25 else 15 if abs(rel7) >= 0.10 else 5 if abs(rel7) >= 0.05 else 0
    if rel30 is not None and abs(rel30) >= 0.50:
        score += 10
    score = min(100, score)

    recent_shock = bool(
        (r7 is not None and abs(r7) >= 0.25)
        or (r30 is not None and abs(r30) >= 0.50)
    )
    post_event_chase_risk = bool(
        (r7 is not None and r7 >= 0.30)
        or (r30 is not None and r30 >= 0.70)
    )

    return {
        "available": True,
        "attention_score": score,
        "attention_label_he": _label_attention(score),
        "last_session_return": one_day,
        "benchmark_last_session_return": bench_day,
        "relative_volume_20d": rel_volume,
        "breakout_20d": breakout_20d,
        "distance_from_20d_high": distance_from_20d_high,
        "context": context,
        "context_label_he": _market_context_label(context),
        "recent_shock": recent_shock,
        "post_event_chase_risk": post_event_chase_risk,
        "recent_return_7d": r7,
        "recent_return_30d": r30,
        "relative_return_7d_vs_xbi": rel7,
        "relative_return_30d_vs_xbi": rel30,
        "heuristic_only": True,
        "note_he": "מדד תשומת לב תיאורי בלבד; אינו משנה את ציון הראיות או את שערי האימות.",
    }


def _event_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _recent_case_return(conn, case: dict, today: date):
    rows = [dict(r) for r in conn.execute(
        "SELECT date,close,volume,source FROM historical_case_prices WHERE case_id=? AND ticker=? ORDER BY date",
        (case["case_id"], case["ticker"]),
    ).fetchall()]
    if not rows:
        return None
    event_day = _event_date(case["event_at"]).isoformat()
    session = case.get("announcement_session") or "unknown"
    if session == "afterhours":
        base = next((r for r in reversed(rows) if r["date"] <= event_day), None)
    else:
        base = next((r for r in reversed(rows) if r["date"] < event_day), None)
    latest = next((r for r in reversed(rows) if r["date"] <= today.isoformat()), None)
    if not base or not latest or not base.get("close"):
        return None
    return latest["close"] / base["close"] - 1


def recent_completed_catalyst(conn, ticker: str, today: date, lookback_days: int = 45) -> dict | None:
    cutoff = (today - timedelta(days=lookback_days)).isoformat()
    row = conn.execute(
        "SELECT * FROM historical_cases WHERE ticker=? AND legacy_post_hoc=0 "
        "AND substr(event_at,1,10)>=? AND substr(event_at,1,10)<=? "
        "ORDER BY event_at DESC LIMIT 1",
        (ticker, cutoff, today.isoformat()),
    ).fetchone()
    if not row:
        return None
    case = dict(row)
    event_day = _event_date(case["event_at"])
    return {
        "case_id": case["case_id"],
        "ticker": case["ticker"],
        "catalyst_type": case["catalyst_type"],
        "event_at": case["event_at"],
        "announcement_session": case.get("announcement_session"),
        "days_since": (today - event_day).days,
        "return_since_event": _recent_case_return(conn, case, today),
        "clean_case": True,
    }


def _chain_entry(row: dict) -> dict:
    window = (row.get("date") or {}).get("window") or {}
    return {
        "id": row.get("id"),
        "ticker": row.get("ticker"),
        "type": row.get("type"),
        "program": row.get("program"),
        "start": window.get("start"),
        "end": window.get("end"),
        "days_to": row.get("days_to"),
        "recommendation": (row.get("recommendation") or {}).get("status"),
        "verification_state": (row.get("state") or {}).get("verification_state"),
    }


def enrich_live_rows(conn, rows: list[dict], today: date) -> list[dict]:
    """Attach observational market context + same-ticker catalyst chain to scored rows."""
    by_ticker: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("ticker"):
            by_ticker.setdefault(row["ticker"], []).append(row)

    for ticker_rows in by_ticker.values():
        ticker_rows.sort(key=lambda r: (((r.get("date") or {}).get("window") or {}).get("start") or "9999", r.get("id") or ""))

    as_of = (today + timedelta(days=1)).isoformat()
    recent_cache: dict[str, dict | None] = {}
    for row in rows:
        ticker = row.get("ticker")
        row["market_intelligence"] = market_intelligence_for_row(conn, row, as_of) if ticker else {"available": False}
        row["catalyst_chain"] = [_chain_entry(x) for x in by_ticker.get(ticker, [])[:5]] if ticker else []
        if ticker not in recent_cache:
            recent_cache[ticker] = recent_completed_catalyst(conn, ticker, today) if ticker else None
        row["recent_completed_catalyst"] = recent_cache.get(ticker)
        recent = row.get("recent_completed_catalyst")
        if recent:
            row["continuity_note_he"] = "הטיקר מופיע בגלל קטליזטור עתידי נוסף; האירוע האחרון כבר פורסם."
        else:
            row["continuity_note_he"] = None
    return rows
