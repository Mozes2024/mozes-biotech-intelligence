"""Point-in-time historical dataset import, evidence controls, and readiness."""
from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from . import db

HORIZONS = ("T-60", "T-30", "T-14", "T-7", "T-3", "T-1")


def market_event_date(event_at: str) -> date:
    """New York date of a UTC announcement, including post-close UTC rollover."""
    stamp = _timestamp(event_at)
    year = stamp.year
    march, november = date(year, 3, 1), date(year, 11, 1)
    second_sunday_march = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
    first_sunday_november = november + timedelta(days=(6 - november.weekday()) % 7)
    dst_start = datetime.combine(second_sunday_march, datetime.min.time(), timezone.utc) + timedelta(hours=7)
    dst_end = datetime.combine(first_sunday_november, datetime.min.time(), timezone.utc) + timedelta(hours=6)
    return (stamp - timedelta(hours=4 if dst_start <= stamp < dst_end else 5)).date()


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _archive_sources(conn, rows, fetch: bool) -> None:
    for source in rows:
        content = source.get("content")
        if content is None and fetch:
            request = Request(source["canonical_url"], headers={"User-Agent": "MOZES historical research archive/0.3"})
            with urlopen(request, timeout=30) as response:
                content = response.read().decode("utf-8", errors="replace")
        if content is None:
            raise ValueError(f"source {source['source_id']} needs archived content (or --fetch-sources)")
        existing = db.source_archive_row(conn, source["source_id"])
        retrieved_at = source.get("retrieved_at") or (existing["retrieved_at"] if existing else db.utcnow())
        db.archive_source(conn, source["source_id"], source["canonical_url"], source["source_type"], source.get("published_at"),
                          retrieved_at, content, source.get("metadata"))


def _prove_pre_event(conn, source_ids: list[str], cutoff: str) -> None:
    for source_id in source_ids:
        source = db.source_archive_row(conn, source_id)
        if not source or not source["published_at"]:
            raise ValueError(f"source {source_id} cannot prove point-in-time availability")
        if _timestamp(source["published_at"]) > _timestamp(cutoff):
            raise ValueError(f"source {source_id} was published after snapshot cutoff")


def import_bundle(conn, path: str | Path, *, fetch_sources: bool = False) -> dict:
    """Import a declarative, idempotent historical bundle without hindsight leakage."""
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    _archive_sources(conn, bundle.get("sources", []), fetch_sources)
    for case in bundle.get("cases", []):
        if "T" not in case["event_at"]:
            raise ValueError(f"case {case['case_id']} needs an announcement timestamp, not only a date")
        event_at, event_time = case["event_at"], _timestamp(case["event_at"])
        case_sources = list(case.get("source_ids", []))
        if not case_sources or any(not db.source_archive_row(conn, s) for s in case_sources):
            raise ValueError(f"case {case['case_id']} has unarchived source provenance")
        db.upsert_historical_case(conn, case["case_id"], case["ticker"], case["catalyst_type"], event_at,
                                  announcement_session=case.get("announcement_session", "unknown"), provenance=case_sources)
        for snap in case.get("feature_snapshots", []):
            if _timestamp(snap["as_of"]) >= event_time:
                raise ValueError(f"snapshot {snap['snapshot_id']} is not strictly before event")
            horizon = snap.get("horizon")
            if horizon in HORIZONS and (market_event_date(event_at) - _timestamp(snap["as_of"]).date()).days != int(horizon[2:]):
                raise ValueError(f"snapshot {snap['snapshot_id']} horizon does not match its as_of date")
            sources = list(snap.get("source_ids", []))
            _prove_pre_event(conn, sources, snap["as_of"])
            if not sources:
                raise ValueError(f"snapshot {snap['snapshot_id']} has no source provenance")
            payload = {**snap.get("features", {}), "horizon": snap.get("horizon")}
            db.store_feature_snapshot(conn, snap["snapshot_id"], case["case_id"], snap["as_of"], payload, provenance=sources, blinded=True)
        outcome = case.get("outcome_label")
        if outcome:
            if _timestamp(outcome["labeled_at"]) < event_time:
                raise ValueError(f"outcome {outcome['label_id']} predates event")
            sources = list(outcome.get("source_ids", []))
            if not sources or any(not db.source_archive_row(conn, s) for s in sources):
                raise ValueError(f"outcome {outcome['label_id']} has unarchived source provenance")
            for source_id in sources:
                published = db.source_archive_row(conn, source_id)["published_at"]
                if not published or _timestamp(published) > _timestamp(outcome["labeled_at"]):
                    raise ValueError(f"outcome source {source_id} was unavailable when label was attached")
            db.store_outcome_label(conn, outcome["label_id"], case["case_id"], outcome["labeled_at"], outcome.get("label", {}), provenance=sources, verified=True)
    return {"cases": len(bundle.get("cases", [])), "sources": len(bundle.get("sources", []))}


def event_price_window(event_at: str) -> tuple[str, str]:
    day = market_event_date(event_at)
    return ((day - timedelta(days=180)).isoformat(), (day + timedelta(days=45)).isoformat())


def attach_price_coverage(conn, case_id: str, provider: str, run_id: str | None = None, benchmark: str = "XBI") -> dict:
    case = next(row for row in db.historical_case_rows(conn) if row["case_id"] == case_id)
    start, end = event_price_window(case["event_at"])
    db.attach_historical_prices(conn, case_id, case["ticker"], start, end, provider, run_id, benchmark)
    return {"case_id": case_id, "ticker": case["ticker"], "benchmark": benchmark, "start": start, "end": end}


def backfill_prices(conn, case_id: str, provider: str, *, stock_file: str | None = None, benchmark_file: str | None = None,
                    source_url: str | None = None, capture_metadata: dict | None = None) -> dict:
    """Fetch or load ticker and XBI prices and bind their provenance to one case."""
    case = next(row for row in db.historical_case_rows(conn) if row["case_id"] == case_id)
    start, end = event_price_window(case["event_at"])
    if provider == "csv":
        if not stock_file or not benchmark_file:
            raise ValueError("csv backfill requires --stock-file and --benchmark-file")
        from .ingest.prices import load_csv
        stock, benchmark = load_csv(stock_file), load_csv(benchmark_file)
        metadata = {"stock_file": Path(stock_file).name, "benchmark_file": Path(benchmark_file).name,
                    "stock_sha256": hashlib.sha256(Path(stock_file).read_bytes()).hexdigest(),
                    "benchmark_sha256": hashlib.sha256(Path(benchmark_file).read_bytes()).hexdigest(),
                    "source_url": source_url or "user-supplied CSV; origin unspecified"}
        if capture_metadata:
            metadata["capture"] = capture_metadata
    elif provider == "yahoo":
        from .ingest.prices import fetch_yahoo_chart
        stock, benchmark = fetch_yahoo_chart(case["ticker"], start, end), fetch_yahoo_chart("XBI", start, end)
        metadata = {"adapter": "yahoo-chart-keyless", "start": start, "end": end}
    else:
        raise ValueError(f"unsupported historical price provider: {provider}")
    run_id = "price-" + hashlib.sha256(f"{case_id}|{provider}|{start}|{end}|{json.dumps(metadata,sort_keys=True)}".encode()).hexdigest()[:16]
    db.store_price_ingestion_run(conn, run_id, provider, metadata)
    db.store_prices(conn, case["ticker"], stock, provider)
    db.store_prices(conn, "XBI", benchmark, provider)
    attached = attach_price_coverage(conn, case_id, provider, run_id)
    return {**attached, "stock_rows": len(stock), "benchmark_rows": len(benchmark), "run_id": run_id}


def attached_price_rows(conn, case: dict) -> tuple[list[dict], list[dict]]:
    """Read only the price values frozen under this case's XBI attachment."""
    a = conn.execute("SELECT * FROM historical_price_attachments WHERE case_id=? AND ticker=? AND benchmark='XBI'",
                     (case["case_id"], case["ticker"])).fetchone()
    if not a:
        return [], []
    def rows(ticker):
        return [dict(r) for r in conn.execute(
            "SELECT date,close,volume,source FROM historical_case_prices "
            "WHERE case_id=? AND ticker=? AND date BETWEEN ? AND ? AND source=? ORDER BY date",
            (case["case_id"], ticker, a["start_date"], a["end_date"], a["provider"])).fetchall()]
    return rows(case["ticker"]), rows(a["benchmark"])


def _price_status(conn, case: dict) -> tuple[bool, str | None]:
    stock, benchmark = attached_price_rows(conn, case)
    if not stock and not benchmark:
        return False, "missing case-bound price attachment or rows"
    event_day = market_event_date(case["event_at"]).isoformat()
    first_needed = (market_event_date(case["event_at"]) - timedelta(days=120)).isoformat()
    def coverage(rows):
        return len(rows) >= 100 and rows[0]["date"] <= first_needed and rows[-1]["date"] >= event_day
    ok = coverage(stock) and coverage(benchmark)
    return (ok, None if ok else "missing price coverage")


def _hold_price_status(conn, case: dict) -> bool:
    event_day = market_event_date(case["event_at"])
    target = (event_day + timedelta(days=30)).isoformat()
    return all(rows and rows[-1]["date"] >= target for rows in attached_price_rows(conn, case))


def _archived_provenance(conn, ids: list[str], cutoff: str | None = None) -> bool:
    if not ids:
        return False
    for source_id in ids:
        source = db.source_archive_row(conn, source_id)
        if not source or not source["content_hash"] or not source["published_at"]:
            return False
        if cutoff and _timestamp(source["published_at"]) > _timestamp(cutoff):
            return False
    return True


def readiness_for_case(conn, case_id: str) -> dict:
    case = next((row for row in db.historical_case_rows(conn) if row["case_id"] == case_id), None)
    if not case:
        raise KeyError(case_id)
    snapshots, labels, reasons = db.feature_snapshot_rows(conn, case_id), db.outcome_label_rows(conn, case_id), []
    if case["legacy_post_hoc"]: reasons.append("legacy post-hoc case is quarantined")
    if not _archived_provenance(conn, json.loads(case["provenance_json"] or "[]")): reasons.append("case provenance missing")
    if not snapshots: reasons.append("missing point-in-time feature snapshot")
    for snap in snapshots:
        if snap["as_of"] >= case["event_at"]: reasons.append("feature snapshot is not strictly before event")
        if not snap["blinded"]: reasons.append("feature snapshot was not blinded")
        if not _archived_provenance(conn, json.loads(snap["provenance_json"] or "[]"), snap["as_of"]):
            reasons.append("feature provenance missing or late")
    if not labels: reasons.append("missing outcome label")
    elif not labels[-1]["verified"]: reasons.append("outcome label is not verified")
    elif not _archived_provenance(conn, json.loads(labels[-1]["provenance_json"] or "[]"), labels[-1]["labeled_at"]):
        reasons.append("outcome provenance missing or late")
    prices_ok, price_reason = _price_status(conn, case)
    if price_reason: reasons.append(price_reason)
    if case["announcement_session"] == "unknown": reasons.append("announcement session unknown")
    research_ready = not reasons
    runup_ready = research_ready
    hold_ready = runup_ready and _hold_price_status(conn, case) and bool(labels and json.loads(labels[-1]["payload"]).get("event_return") is not None)
    return {"case_id": case_id, "research_ready": research_ready, "runup_ready": runup_ready, "hold_ready": hold_ready, "prices_ok": prices_ok, "reasons": sorted(set(reasons))}


def readiness_summary(conn) -> dict:
    rows, cases = [readiness_for_case(conn, c["case_id"]) for c in db.historical_case_rows(conn)], db.historical_case_rows(conn)
    return {"cases": len(rows), "research_ready": sum(r["research_ready"] for r in rows), "runup_ready": sum(r["runup_ready"] for r in rows), "hold_ready": sum(r["hold_ready"] for r in rows), "missing_prices": sum(not r["prices_ok"] for r in rows), "missing_provenance": sum(any("provenance" in x for x in r["reasons"]) for r in rows), "unknown_session": sum(c["announcement_session"] == "unknown" for c in cases), "legacy_quarantined": sum(bool(c["legacy_post_hoc"]) for c in cases), "rows": rows}
