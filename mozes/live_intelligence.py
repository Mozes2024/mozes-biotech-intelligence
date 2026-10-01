"""Lightweight market context and catalyst-chain enrichment for live biotech events.

This layer is observational. It does not change clinical/regulatory evidence scores or
empirical validation gates. It answers practical monitoring questions: did the stock
already move, is the move stock-specific or broad biotech, are prices fresh, and which
future catalyst keeps the ticker relevant after a prior event has resolved.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import db
from .market import join_on_dates


MAX_PRICE_AGE_DAYS = 5


def _ret(a, b):
    if not a or not b or not a.get("close") or not b.get("close"):
        return None
    return b["close"] / a["close"] - 1


def _label_attention(score: int | None) -> str:
    if score is None:
        return "נתונים ישנים"
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
        "sector_supported": "תנועה רחבה יחסית בסקטור",
        "sector_aligned": "דומה יחסית לתנועת הסקטור",
        "mixed": "הקשר מעורב",
        "unknown": "אין מספיק נתונים",
    }.get(code, code)


def _is_us_market_open(now_utc: datetime, latest_day: date) -> bool:
    try:
        eastern = ZoneInfo("America/New_York")
    except ZoneInfoNotFoundError:
        # Minimal Windows/Python environments may lack the IANA zone database.
        # US DST dates keep this fallback correct for supported current-era runs.
        eastern = timezone(timedelta(hours=-4))
    ny = now_utc.astimezone(eastern)
    return latest_day == ny.date() and ny.weekday() < 5 and time(9, 30) <= ny.time() < time(16, 0)


def _watch_breadth(conn, as_of: str, today: date) -> dict:
    moves = []
    for watch in db.watch_rows(conn):
        ticker = watch.get("ticker")
        if not ticker:
            continue
        rows = db.load_prices(conn, ticker, before=as_of)
        if len(rows) < 2:
            continue
        try:
            last_day = date.fromisoformat(rows[-1]["date"])
        except Exception:
            continue
        if (today - last_day).days > MAX_PRICE_AGE_DAYS:
            continue
        r = _ret(rows[-2], rows[-1])
        if r is not None:
            moves.append((ticker, r))
    return {
        "sample_n": len(moves),
        "up_3pct": sum(r >= 0.03 for _, r in moves),
        "down_3pct": sum(r <= -0.03 for _, r in moves),
        "moves": moves,
    }


def market_intelligence_for_row(conn, row: dict, as_of: str, benchmark: str = "XBI", *,
                                today: date | None = None, now_utc: datetime | None = None) -> dict:
    """Build descriptive market-attention context from shared live prices.

    Thresholds are transparent heuristics for attention/noise filtering only. They are not
    alpha estimates and never alter evidence scores or validation gates.
    """
    ticker = row.get("ticker")
    if not ticker:
        return {"available": False, "reason": "missing ticker"}

    today = today or (date.fromisoformat(as_of[:10]) - timedelta(days=1))
    now_utc = now_utc or datetime.now(timezone.utc)
    stock = db.load_prices(conn, ticker, before=as_of)
    bench = db.load_prices(conn, benchmark, before=as_of)
    if len(stock) < 2:
        return {"available": False, "reason": "fewer than 2 closes", "ticker": ticker}

    last, prev = stock[-1], stock[-2]
    latest_day = date.fromisoformat(last["date"])
    price_age_days = max(0, (today - latest_day).days)
    stale = price_age_days > MAX_PRICE_AGE_DAYS
    session_in_progress = _is_us_market_open(now_utc, latest_day)

    one_day = _ret(prev, last)
    prior20 = stock[max(0, len(stock) - 21):-1]
    vols = [float(x["volume"]) for x in prior20 if x.get("volume") not in (None, 0)]
    med_vol = median(vols) if vols else None
    # Comparing an intraday partial volume with full prior sessions is misleading. Keep the
    # return, but suppress relative-volume scoring until a completed daily bar is available.
    rel_volume = None
    if not session_in_progress and med_vol and last.get("volume"):
        rel_volume = float(last["volume"]) / med_vol
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

    breadth = _watch_breadth(conn, as_of, today)
    peer_moves = [(t, r) for t, r in breadth["moves"] if t != ticker]
    peer_n = len(peer_moves)
    same_direction_share = None
    if one_day is not None and peer_n:
        if one_day > 0:
            same_direction_share = sum(r >= 0.03 for _, r in peer_moves) / peer_n
        elif one_day < 0:
            same_direction_share = sum(r <= -0.03 for _, r in peer_moves) / peer_n

    context = "unknown"
    if one_day is not None and not stale:
        same_direction = bench_day is not None and one_day * bench_day > 0
        broad_watch = peer_n >= 4 and (same_direction_share or 0) >= 0.25
        broad_xbi = same_direction and abs(bench_day or 0) >= 0.015
        if abs(one_day) >= 0.03 and not broad_watch and not broad_xbi:
            context = "stock_specific"
        elif abs(one_day) >= 0.03 and (broad_watch or broad_xbi):
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
    attention_score = None if stale else score

    recent_shock = None if stale else bool(
        (r7 is not None and abs(r7) >= 0.25)
        or (r30 is not None and abs(r30) >= 0.50)
    )
    post_event_chase_risk = None if stale else bool(
        (r7 is not None and r7 >= 0.30)
        or (r30 is not None and r30 >= 0.70)
    )

    return {
        "available": True,
        "price_date": last["date"],
        "price_age_days": price_age_days,
        "price_fresh": not stale,
        "session_in_progress": session_in_progress,
        "volume_partial_suppressed": session_in_progress,
        "attention_score": attention_score,
        "attention_label_he": _label_attention(attention_score),
        "last_session_return": one_day,
        "benchmark_last_session_return": bench_day,
        "relative_volume_20d": rel_volume,
        "breakout_20d": breakout_20d,
        "distance_from_20d_high": distance_from_20d_high,
        "context": context,
        "context_label_he": _market_context_label(context),
        "breadth_sample_n": peer_n,
        "same_direction_big_move_share": same_direction_share,
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
    frozen = [dict(r) for r in conn.execute(
        "SELECT date,close,volume,source FROM historical_case_prices WHERE case_id=? AND ticker=? ORDER BY date",
        (case["case_id"], case["ticker"]),
    ).fetchall()]
    if not frozen:
        return None
    event_day = _event_date(case["event_at"]).isoformat()
    session = case.get("announcement_session") or "unknown"
    if session == "afterhours":
        base = next((r for r in reversed(frozen) if r["date"] <= event_day), None)
    else:
        base = next((r for r in reversed(frozen) if r["date"] < event_day), None)
    # The baseline is case-bound and frozen; the latest observation must come from the live
    # shared-price table, otherwise a captured historical window masquerades as a current move.
    live = db.load_prices(conn, case["ticker"], before=(today + timedelta(days=1)).isoformat())
    latest = live[-1] if live else None
    if not base or not latest or latest["date"] <= base["date"] or not base.get("close"):
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
    now_utc = datetime.now(timezone.utc)
    for row in rows:
        ticker = row.get("ticker")
        row["market_intelligence"] = market_intelligence_for_row(
            conn, row, as_of, today=today, now_utc=now_utc
        ) if ticker else {"available": False}
        row["catalyst_chain"] = [_chain_entry(x) for x in by_ticker.get(ticker, [])[:5]] if ticker else []
        if ticker not in recent_cache:
            recent_cache[ticker] = recent_completed_catalyst(conn, ticker, today) if ticker else None
        row["recent_completed_catalyst"] = recent_cache.get(ticker)
        recent = row.get("recent_completed_catalyst")
        row["continuity_note_he"] = (
            "הטיקר מופיע בגלל קטליזטור עתידי נוסף; האירוע האחרון כבר פורסם."
            if recent else None
        )
    return rows
