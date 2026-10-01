"""Database-driven radar orchestration for v0.2.

This layer converts the legacy curated seed into first-class database events, attaches
provenance/state, and reads prices from SQLite. Live discovery can add candidates without
silently turning registry estimates into verified catalysts.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import db
from .data_loader import load_historical, load_live, load_outcomes, load_sources
from .dates import date_info
from .lifecycle import is_actionable, is_resolved
from .source_quality import verification_state
from .validation import gate_summary

DATA_DIR = Path(__file__).parent / "data"


def canonical_source_type(src: dict) -> str:
    st = (src.get("source_type") or "unknown").lower()
    rel = (src.get("reliability") or "").lower()
    if st.startswith("sec"):
        return "sec"
    if st in {"press_release", "earnings_release"} and rel == "primary":
        return "company_press_release" if st == "press_release" else "company_ir"
    if st == "research":
        return "peer_reviewed"
    if st in {"aggregator", "news"}:
        return st
    return st


def _load_overrides():
    p = DATA_DIR / "event_overrides_v02.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _load_security_overrides():
    p = DATA_DIR / "security_overrides.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def bootstrap_database(conn):
    """Seed once, then attach event state/provenance and current corrections."""
    if db.count_events(conn) == 0:
        db.seed(conn, load_sources(), load_live(), load_historical(), load_outcomes())
    sources = load_sources()
    overrides = _load_overrides()
    for event in db.load_events(conn):
        state = db.event_state(conn, event["id"])
        if state:
            continue
        ev_sources = []
        for c in event.get("chronology", []):
            sid = c.get("src")
            s = sources.get(sid) if sid else None
            if not s:
                continue
            st = canonical_source_type(s)
            rec = {
                "source_type": st,
                "published_at": s.get("published"),
                "url": s.get("url"),
                "source_id": sid,
            }
            ev_sources.append(rec)
            db.add_event_source(conn, event["id"], sid, st, s.get("url"), s.get("published"), c.get("text"), bool(c.get("date_text")))
        di = date_info(event.get("chronology", []), sources)
        vs = verification_state(ev_sources, di.get("precision"))
        status = "VERIFIED" if vs["state"] == "VERIFIED" else vs["state"]
        if di.get("precision") == "exact" and status == "VERIFIED":
            status = "SCHEDULED"
        ov = overrides.get(event["id"], {})
        db.upsert_event_state(
            conn,
            event["id"],
            status=ov.get("status", status),
            verification_state=ov.get("verification_state", vs["state"]),
            verification_confidence=ov.get("verification_confidence", vs["confidence"]),
            event_timestamp=ov.get("event_timestamp"),
            event_session=ov.get("event_session", "unknown"),
            resolved_at=ov.get("resolved_at"),
            note=ov.get("note", vs["reason"]),
        )
        for extra in ov.get("sources", []):
            db.add_event_source(conn, event["id"], extra["source_id"], extra["source_type"], extra.get("url"), extra.get("published_at"), extra.get("statement"), extra.get("supports_date", True))

    # Historical tickers are retained in historical evidence, not current monitoring.
    for event in db.load_events(conn, "live"):
        if event.get("ticker"):
            db.upsert_watch(conn, event["ticker"], event.get("company"), source="curated-catalog")
    with conn:
        conn.execute("UPDATE watch_universe SET active=0 WHERE source='curated-catalog' AND ticker NOT IN "
                     "(SELECT DISTINCT json_extract(payload,'$.ticker') FROM events WHERE kind='live')")
    for ticker, rec in _load_security_overrides().items():
        db.upsert_security_lifecycle(conn, ticker, **{k: v for k, v in rec.items() if k != "asset"})
        if rec.get("asset"):
            asset = rec["asset"]
            db.upsert_asset_ownership(conn, asset["asset_id"], asset["owner_ticker"], rec["effective_from"], relationship=asset["relationship"], source_url=rec["source_url"], source_type=rec["source_type"], published_at=rec.get("published_at"))

    # The original catalog is useful as a migration source, never as validation
    # evidence.  It is represented explicitly and remains quarantined until a
    # blinded snapshot and source-provenanced outcome are supplied.
    for event in db.load_events(conn, "historical"):
        db.upsert_historical_case(
            conn, event["id"], event.get("ticker") or "UNKNOWN", event.get("type") or "UNKNOWN", event["date"],
            legacy_event_id=event["id"], announcement_session="unknown", legacy_post_hoc=True,
        )

    if not db.validation_row(conn, "runup"):
        db.set_validation(conn, "runup", False, 0, 150, note="locked until stable positive out-of-sample evidence")
    if not db.validation_row(conn, "hold_through"):
        db.set_validation(conn, "hold_through", False, 0, 200, note="locked until calibrated out-of-sample binary model")


def live_event_records(conn, include_quarantined=True):
    events = db.load_events(conn, "live")
    out = []
    for e in events:
        security = __import__('mozes.security', fromlist=['tradability']).tradability(conn, e.get("ticker"))
        if security["status"] in {"ACQUIRED", "DELISTED", "SUSPENDED", "BANKRUPT", "RENAMED"}:
            continue
        s = db.event_state(conn, e["id"]) or {}
        if is_resolved(s.get("status", "")):
            continue
        if not include_quarantined and not is_actionable(s.get("status", "")):
            continue
        row = dict(e)
        row["state"] = s
        row["sources_v2"] = db.load_event_sources(conn, e["id"])
        row["prices_db"] = db.load_prices(conn, e.get("ticker")) if e.get("ticker") else []
        row["security"] = security
        out.append(row)
    return out


def current_resolved_records(conn):
    out = []
    for e in db.load_events(conn, "live"):
        s = db.event_state(conn, e["id"]) or {}
        security = __import__('mozes.security', fromlist=['tradability']).tradability(conn, e.get("ticker"))
        if is_resolved(s.get("status", "")) or security["status"] in {"ACQUIRED", "DELISTED", "SUSPENDED", "BANKRUPT", "RENAMED"}:
            out.append({**e, "state": s, "security": security, "sources_v2": db.load_event_sources(conn, e["id"])})
    return out


def validation_status(conn):
    def row(name, default_min):
        r = db.validation_row(conn, name)
        if not r:
            return {"enabled": False, "satisfied": False, "n": 0, "min_n": default_min}
        return {"enabled": bool(r["enabled"]), "satisfied": bool(r["enabled"]) and r["oos_n"] >= r["min_oos_n"],
                "n": r["oos_n"], "min_n": r["min_oos_n"], "note": r.get("note")}
    return {"runup": row("runup", 150), "hold_through": row("hold_through", 200)}
