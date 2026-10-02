"""Conservative candidate promotion from registry discovery to verified events."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from . import db
from .universe import normalize_org
from .source_quality import is_primary
from .lifecycle import is_resolved


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
    data = json.loads(raw or "{}")
    ps = data.get("protocolSection", data)
    acronym = (ps.get("identificationModule") or {}).get("acronym")
    interventions = [x.get("name", "") for x in (ps.get("armsInterventionsModule") or {}).get("interventions", [])]
    names = ([acronym] if acronym else []) + interventions
    generic = {"placebo", "standard of care", "best available therapy", "chemotherapy"}
    specific = [name for name in names if len(name) >= 3 and name.lower() not in generic
                and re.search(r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])", text)]
    if specific:
        return True, .90, ["exact registered program/intervention: " + specific[0]]
    terms = _terms(candidate)
    hits = [t for t in terms if t.lower() in text]
    if hits:
        reasons.append("program/intervention match: " + ", ".join(hits[:3]))
    score = min(0.95, 0.55 + 0.12 * len(hits)) if hits else 0.0
    return score >= 0.79, score, reasons


def promote_candidate(conn, candidate: dict, extracted: dict, ticker: str, source_type="sec"):
    def reject(reason, **details):
        from .intelligence_store import digest, encode
        decision_id = digest([ticker, candidate.get('candidate_id'), extracted.get('source_id'), extracted.get('source_url'), reason])
        with conn:
            conn.execute("INSERT OR IGNORE INTO v2e2_promotion_decisions(decision_id,ticker,candidate_id,evidence_id,accession,outcome,reason,details,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (decision_id, ticker, candidate.get('candidate_id'), None, extracted.get('accession'), 'REJECTED', reason, encode(details or extracted), db.utcnow()))
        return None
    if not is_primary(source_type) or extracted.get("reliability") == "secondary":
        return reject('non_primary_source')
    source_url = extracted.get("source_url") or extracted.get("source_id")
    if not source_url or not source_url.startswith("https://"):
        return reject('missing_or_invalid_source_url')
    ok, confidence, reasons = candidate_matches_statement(candidate, extracted.get("statement", ""))
    if not ok:
        return reject('no_candidate_match', confidence=confidence, reasons=reasons)
    w = extracted.get("window") or {}
    event_driven = extracted.get("timing_mode") == "EVENT_DRIVEN"
    if not event_driven and w.get("precision", "unknown") == "unknown":
        return reject('unbounded_calendar_precision')
    event_id = f"AUTO-{ticker}-{candidate.get('nct_id') or candidate['candidate_id']}"
    previous = conn.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
    prior_event = json.loads(previous["payload"]) if previous else {}
    state = db.event_state(conn, event_id) or {}
    if is_resolved(state.get("status", "")):
        return event_id
    published = extracted.get("published_at") or extracted.get("filed")
    if not published:
        return reject('missing_publication_timestamp')
    if published < prior_event.get("guidance_published_at", ""):
        return event_id
    trigger = {key: value for key, value in extracted.items()
               if key.startswith("trigger_") or key in {"timing_mode", "monitoring_state"}}
    if event_driven:
        trigger.update(trigger_as_of=trigger.get("trigger_as_of") or published, trigger_source_id=extracted.get("source_id"),
                       trigger_source_url=source_url)
        if trigger.get("trigger_current") is None and prior_event.get("trigger_target") == trigger.get("trigger_target"):
            for key in ("trigger_current", "trigger_as_of", "monitoring_state", "trigger_source_id", "trigger_source_url", "trigger_quote"):
                if key in prior_event:
                    trigger[key] = prior_event[key]
    event = {
        "id": event_id,
        "ticker": ticker,
        "company": candidate.get("sponsor"),
        "program": candidate.get("title"),
        "indication": None,
        "ta": None,
        "type": (("P3_TOPLINE" if "PHASE3" in (candidate.get("phase") or "") else "P2_TOPLINE")
                 if extracted.get("catalyst_type") in {"OTHER", "READOUT", None} else extracted["catalyst_type"]),
        "phase": candidate.get("phase"),
        "pivotal": "PHASE3" in (candidate.get("phase") or ""),
        "mcap": "unknown",
        "dependency": None,
        "commercial": False,
        "enables_filing": False,
        "features": {},
        "features_as_of": None,
        "documents": [], "flags": [], "prices": [],
        "chronology": [{"date": published[:10],
                         "date_text": w.get("original"), "src": extracted.get("source_id"),
                         "text": extracted.get("statement")}],
        "auto_discovered": True,
        "nct_id": candidate.get("nct_id"), "guidance_published_at": published,
        "verified_window": None if event_driven else w,
        "timing_mode": "CALENDAR", **trigger,
        "provenance": {"source_type": source_type, "source_url": source_url,
                       "published_at": published, "retrieved_at": extracted.get("retrieved_at") or db.utcnow(),
                       "quote": extracted.get("statement"), "verification_method": "deterministic_program_match",
                       "primary": True, "accession": extracted.get("accession"), "nct_id": candidate.get("nct_id"),
                       "date_precision": w.get("precision"), "trigger_precision": trigger.get("trigger_precision"),
                       "sec_accepted_at": extracted.get("accepted")},
    }
    current_facet = {"source_url": source_url, "source_id": extracted.get("source_id"),
                     "timing_mode": extracted.get("timing_mode"), "window": w,
                     "trigger": trigger, "published_at": published}
    event["timing_facets"] = [current_facet]
    if previous:
        # A progress observation must not erase accumulated clinical/financial inputs.
        for key in ("features", "features_as_of", "documents", "flags", "prices", "indication", "ta",
                    "mcap", "dependency", "commercial", "enables_filing"):
            if key in prior_event:
                event[key] = prior_event[key]
        chronology = list(prior_event.get("chronology") or [])
        if event["chronology"][0] not in chronology:
            chronology.extend(event["chronology"])
        event["chronology"] = chronology
        prior_facets = list(prior_event.get("timing_facets") or [])
        fingerprints = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in prior_facets}
        if json.dumps(current_facet, sort_keys=True, ensure_ascii=False) not in fingerprints:
            prior_facets.append(current_facet)
        event["timing_facets"] = prior_facets
    with conn:
        conn.execute("INSERT INTO events(id,kind,payload) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (event_id, "live", json.dumps(event, ensure_ascii=False)))
        conn.execute("UPDATE discovery_candidates SET promoted_event_id=? WHERE candidate_id=?", (event_id, candidate["candidate_id"]))
    source_id = extracted.get("source_id") or f"sec-{event_id}"
    if not conn.execute("SELECT 1 FROM event_sources WHERE event_id=? AND source_id=? AND statement=?",
                        (event_id, source_id, extracted.get("statement"))).fetchone():
        db.add_event_source(conn, event_id, source_id, source_type,
                            source_url, published, extracted.get("statement"), not event_driven)
    db.upsert_event_state(conn, event_id, status="SCHEDULED" if w.get("precision") == "exact" else "VERIFIED",
                          verification_state="VERIFIED", verification_confidence=min(100, int(90 + confidence * 10)),
                          event_timestamp=state.get("event_timestamp"), event_session=state.get("event_session", "unknown"),
                          note="auto-promoted only after primary-source match: " + "; ".join(reasons))
    from .live_monitor import record_change
    changed = "catalyst_verified" if not previous else "trigger_progressed" if prior_event.get("trigger_current") != event.get("trigger_current") else "primary_statement_added"
    if previous and prior_event.get("verified_window") != event.get("verified_window"):
        changed = "date_moved"
    record_change(conn, ticker=ticker, event_id=event_id, change_type=changed,
                  previous_value=prior_event.get("trigger_current") if previous else None,
                  new_value=event.get("trigger_current") if changed == "trigger_progressed" else extracted.get("statement"),
                  source_url=source_url, source_type=source_type, verification_state="primary_source",
                  identity=[event_id, source_url, extracted.get("statement")])
    db.upsert_watch(conn, ticker, candidate.get("sponsor"), source="primary_verified_candidate")
    return event_id
