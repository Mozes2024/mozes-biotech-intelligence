"""Deterministic, source-first 2024–2026 case batch assembly and import."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .historical import HORIZONS, backfill_prices, import_bundle, market_event_date, readiness_summary

DATA = Path(__file__).resolve().parent / "data"
FRAMES = ("historical_candidate_frame_2024_2026.json", "historical_candidate_frame_2024_2026_b.json")
ADJUDICATION = "historical_candidate_adjudication_2024_2026.json"
BUNDLE = "historical_candidate_bundle_2024_2026.json"
PRICE_DIR = "candidate_prices_2024_2026"
PROVIDER_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"


def load_frame(data_dir: Path = DATA) -> dict[str, dict]:
    """Selection is determined by frozen, outcome-free frame fields only."""
    candidates = {}
    for filename in FRAMES:
        frame = json.loads((data_dir / filename).read_text(encoding="utf-8"))
        allowed = set(frame["selection_fields_only"])
        if allowed != {"candidate_id", "ticker", "catalyst_type", "planned_date", "pre_event_url"}:
            raise ValueError("candidate frame fields changed")
        for candidate in frame["candidates"]:
            if set(candidate) != allowed or candidate["candidate_id"] in candidates:
                raise ValueError("duplicate candidate or outcome/price field in frozen frame")
            candidates[candidate["candidate_id"]] = candidate
    return candidates


def inclusion_ids(data_dir: Path = DATA) -> list[str]:
    """Fixed evidence-availability decision; labels and returns are never consulted."""
    candidates = load_frame(data_dir)
    adjudication = json.loads((data_dir / ADJUDICATION).read_text(encoding="utf-8"))
    included = [row["candidate_id"] for row in adjudication["included"]]
    excluded = adjudication["excluded"]
    if len(included) != len(set(included)) or set(included) & set(excluded):
        raise ValueError("candidate has conflicting adjudication")
    if set(included) | set(excluded) != set(candidates):
        raise ValueError("every frozen candidate needs one disposition")
    if any(not reason.startswith(("missing_case_bound_prices:", "missing_exact_primary_announcement_timestamp:"))
           for reason in excluded.values()):
        raise ValueError("exclusion must state an evidence-availability reason")
    return sorted(included)


def _market_session(event_at: str) -> str:
    # UTC release time is paired with a documented ET source timestamp. US DST
    # at these dates is verified in the source ledger's event_at field.
    t = datetime.fromisoformat(event_at.replace("Z", "+00:00"))
    day = t.date()
    march = date(day.year, 3, 1)
    november = date(day.year, 11, 1)
    second_sunday_march = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
    first_sunday_november = november + timedelta(days=(6 - november.weekday()) % 7)
    dst_start = datetime.combine(second_sunday_march, datetime.min.time(), timezone.utc) + timedelta(hours=7)
    dst_end = datetime.combine(first_sunday_november, datetime.min.time(), timezone.utc) + timedelta(hours=6)
    local = t - timedelta(hours=4 if dst_start <= t < dst_end else 5)
    if local.hour < 9 or (local.hour == 9 and local.minute < 30):
        return "premarket"
    if local.hour < 16:
        return "intraday"
    return "afterhours"


def build_bundle(data_dir: Path = DATA) -> dict:
    frame = load_frame(data_dir)
    adjudication = json.loads((data_dir / ADJUDICATION).read_text(encoding="utf-8"))
    selected = set(inclusion_ids(data_dir))
    sources, cases = [], []
    for row in adjudication["included"]:
        candidate_id = row["candidate_id"]
        if candidate_id not in selected:
            raise ValueError("unselected candidate in evidence ledger")
        candidate = frame[candidate_id]
        event_at = row["event_at"]
        published_pre = row["pre_published_date"] + "T23:59:59Z"
        if published_pre >= event_at or not row["outcome_url"].startswith("https://"):
            raise ValueError("missing chronological or HTTPS source evidence")
        case_id = "PIT-" + candidate_id
        pre_id, post_id = candidate_id + "-pre", candidate_id + "-outcome"
        sources.extend((
            {"source_id": pre_id, "canonical_url": candidate["pre_event_url"], "source_type": "issuer_distributed_release",
             "published_at": published_pre, "retrieved_at": adjudication["adjudicated_at"], "content": row["pre_fact"],
             "metadata": {"archive_scope": "normalized_fact_extract", "timestamp_basis": "issuer release date; conservative UTC end-of-day", "candidate_frame": "source-first"}},
            {"source_id": post_id, "canonical_url": row["outcome_url"], "source_type": "issuer_distributed_release",
             "published_at": event_at, "retrieved_at": adjudication["adjudicated_at"], "content": row["outcome_fact"],
             "metadata": {"archive_scope": "normalized_fact_extract", "timestamp_basis": "issuer-distributed release minute in ET converted to UTC", "candidate_frame": "source-first"}},
        ))
        pre_evidence = [(pre_id, published_pre, row["pre_fact"])]
        for index, update in enumerate(row.get("pre_updates", []), start=1):
            update_id = candidate_id + f"-pre-update-{index}"
            published = update["published_date"] + "T23:59:59Z"
            if published >= event_at:
                raise ValueError(f"late pre-event update for {candidate_id}")
            pre_evidence.append((update_id, published, update["fact"]))
            sources.append({"source_id": update_id, "canonical_url": update["url"],
                            "source_type": "issuer_distributed_release", "published_at": published,
                            "retrieved_at": adjudication["adjudicated_at"], "content": update["fact"],
                            "metadata": {"archive_scope": "normalized_fact_extract",
                                         "timestamp_basis": "issuer release date; conservative UTC end-of-day",
                                         "candidate_frame": "source-first"}})
        snapshots = []
        for horizon in HORIZONS:
            days = int(horizon[2:])
            cutoff = (market_event_date(event_at) - timedelta(days=days)).isoformat() + "T23:59:59Z"
            available = [(source_id, fact) for source_id, published, fact in pre_evidence if published <= cutoff]
            if not available:
                continue
            snapshots.append({"snapshot_id": case_id + "-" + horizon, "horizon": horizon, "as_of": cutoff,
                              "source_ids": [source_id for source_id, _ in available],
                              "features": {"known_pre_event_facts": [fact for _, fact in available]}})
        if not snapshots:
            raise ValueError(f"{candidate_id} has no source-supported horizon")
        domain = "clinical" if "TOPLINE" in candidate["catalyst_type"] else "regulatory"
        cases.append({"case_id": case_id, "ticker": candidate["ticker"], "catalyst_type": candidate["catalyst_type"],
                      "event_at": event_at, "announcement_session": _market_session(event_at),
                      "source_ids": [source_id for source_id, _, _ in pre_evidence] + [post_id],
                      "feature_snapshots": snapshots,
                      "outcome_label": {"label_id": case_id + "-RESULT", "labeled_at": adjudication["adjudicated_at"],
                                        "source_ids": [post_id], "label": {domain: row["outcome"], "source_fact": row["outcome_fact"]}}})
    return {"sources": sources, "cases": cases}


def checked_bundle(data_dir: Path = DATA) -> Path:
    path = data_dir / BUNDLE
    expected = json.dumps(build_bundle(data_dir), ensure_ascii=False, indent=2) + "\n"
    if path.read_text(encoding="utf-8") != expected:
        raise ValueError("checked-in historical bundle diverges from frozen frame/evidence ledger")
    return path


def import_checked_batch(conn, data_dir: Path = DATA) -> dict:
    """Import checked-in sources/cases and hash-verified ticker/XBI CSVs."""
    path = checked_bundle(data_dir)
    prices = data_dir / PRICE_DIR
    manifest = json.loads((prices / "manifest.json").read_text(encoding="utf-8"))
    for ticker, record in manifest["files"].items():
        actual = hashlib.sha256((prices / f"{ticker}.csv").read_bytes()).hexdigest()
        if actual != record["sha256"]:
            raise ValueError(f"price CSV changed: {ticker}")
    result = import_bundle(conn, path)
    for case in build_bundle(data_dir)["cases"]:
        ticker = case["ticker"]
        if ticker not in manifest["files"] or "XBI" not in manifest["files"]:
            raise ValueError(f"price capture missing for {ticker}")
        backfill_prices(conn, case["case_id"], "csv", stock_file=str(prices / f"{ticker}.csv"),
                        benchmark_file=str(prices / "XBI.csv"), source_url=PROVIDER_URL,
                        capture_metadata={"captured_at": manifest["captured_at"],
                                          "request_start": manifest["request_start"],
                                          "request_end_exclusive": manifest["request_end_exclusive"]})
    return {**result, "readiness": readiness_summary(conn)}


def batch_status(conn, data_dir: Path = DATA) -> dict:
    frame = load_frame(data_dir)
    adjudication = json.loads((data_dir / ADJUDICATION).read_text(encoding="utf-8"))
    selected = inclusion_ids(data_dir)
    ready = readiness_summary(conn)
    cases = {row["case_id"]: row for row in ready["rows"]}
    labels = {row["candidate_id"]: row["outcome"] for row in adjudication["included"]}
    return {"frame_candidates": len(frame), "new_cases": len(selected), "included_ids": selected,
            "excluded": adjudication["excluded"],
            "by_catalyst_type": dict(sorted(Counter(frame[c]["catalyst_type"] for c in selected).items())),
            "by_year": dict(sorted(Counter(row["event_at"][:4] for row in adjudication["included"]).items())),
            "by_outcome": dict(sorted(Counter(labels[c] for c in selected).items())),
            "missing_reason": dict(sorted(Counter(reason.split(":", 1)[0] for reason in adjudication["excluded"].values()).items())),
            "readiness": {key: ready[key] for key in ("cases", "research_ready", "runup_ready", "hold_ready", "legacy_quarantined")},
            "batch_readiness": dict(sorted(Counter("runup_ready" if cases.get("PIT-" + c, {}).get("runup_ready") else "not_ready" for c in selected).items()))}
