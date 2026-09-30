"""Conservative candidate promotion from registry discovery to verified events."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import db
from .universe import normalize_org
from .session import session_from_sec_acceptance


def _terms(candidate: dict):
    raw = json.loads(candidate.get("raw_json") or "{}")
    ps = raw.get("protocolSection", raw)
    arms = ps.get("armsInterventionsModule", {}) if isinstance(ps, dict) else {}
    names = [x.get("name") for x in arms.get("interventions", []) if x.get("name")]
    title = candidate.get("title") or ""
    # Exclude generic trial words; keep drug/program-like tokens.
    toks = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", title) if t.lower() not in {"study","phase","trial","randomized","double","blind","patients","subjects","treatment"}]
    return [x for x in names + toks[:12] if x]


def candidate_matches_statement(candidate: dict, statement: str) -> tuple[bool, float, list[str]]:
    text = statement.lower()
    reasons = []
    raw = candidate.get("raw_json") or ""
    nct = candidate.get("nct_id")
    if nct and nct.lower() in text:
        return True, 1.0, ["exact NCT identifier"]
    terms = _terms(candidate)
    hits = [t for t in terms if t.lower() in text]
    if hits:
        reasons.append("program/intervention match: " + ", ".join(hits[:3]))
    score = min(0.95, 0.55 + 0.12 * len(hits)) if hits else 0.0
    return score >= 0.79, score, reasons


def promote_candidate(conn, candidate: dict, extracted: dict, ticker: str, source_type="sec"):
    ok, confidence, reasons = candidate_matches_statement(candidate, extracted.get("statement", ""))
    if not ok:
        return None
    w = extracted.get("window") or {}
    if w.get("precision") == "unknown":
        return None
    event_id = f"AUTO-{ticker}-{candidate.get('nct_id') or candidate['candidate_id']}"
    event = {
        "id": event_id,
        "ticker": ticker,
        "company": candidate.get("sponsor"),
        "program": candidate.get("title"),
        "indication": None,
        "ta": None,
        "type": extracted.get("catalyst_type") or "READOUT",
        "phase": candidate.get("phase"),
        "pivotal": "PHASE3" in (candidate.get("phase") or ""),
        "mcap": "unknown",
        "dependency": None,
        "commercial": False,
        "enables_filing": False,
        "features": {},
        "features_as_of": None,
        "documents": [], "flags": [], "prices": [],
        "chronology": [{"date": extracted.get("filed") or datetime.now(timezone.utc).date().isoformat(),
                         "date_text": w.get("original"), "src": extracted.get("source_id"),
                         "text": extracted.get("statement")}],
        "auto_discovered": True,
    }
    with conn:
        conn.execute("INSERT OR REPLACE INTO events(id,kind,payload) VALUES(?,?,?)", (event_id, "live", json.dumps(event, ensure_ascii=False)))
        conn.execute("UPDATE discovery_candidates SET promoted_event_id=? WHERE candidate_id=?", (event_id, candidate["candidate_id"]))
    db.add_event_source(conn, event_id, extracted.get("source_id") or f"sec-{event_id}", source_type,
                        extracted.get("source_id"), extracted.get("filed"), extracted.get("statement"), True)
    db.upsert_event_state(conn, event_id, status="SCHEDULED" if w.get("precision") == "exact" else "VERIFIED",
                          verification_state="VERIFIED", verification_confidence=min(100, int(90 + confidence * 10)),
                          event_timestamp=extracted.get("accepted"), event_session=session_from_sec_acceptance(extracted.get("accepted")),
                          note="auto-promoted only after primary-source match: " + "; ".join(reasons))
    return event_id
