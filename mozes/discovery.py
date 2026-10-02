"""ClinicalTrials.gov candidate discovery.

Registry primary-completion dates are sponsor estimates. They create DISCOVERY candidates,
not verified catalyst dates. Promotion requires primary-source verification elsewhere.
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .universe import best_mapping
from .source_observability import record

CTGOV_STUDIES = "https://clinicaltrials.gov/api/v2/studies"
ACTIVE = "RECRUITING,ACTIVE_NOT_RECRUITING,ENROLLING_BY_INVITATION,NOT_YET_RECRUITING"


def _candidate_id(nct_id: str, primary_completion: str | None) -> str:
    raw = f"ctgov|{nct_id}|{primary_completion or 'unknown'}".encode()
    return "CTGOV-" + hashlib.sha1(raw).hexdigest()[:16]


def query_url(start: str, end: str, page_size=1000, page_token=None, statuses=ACTIVE):
    advanced = (
        f"AREA[PrimaryCompletionDate]RANGE[{start},{end}] AND "
        "AREA[Phase](PHASE2 OR PHASE3) AND AREA[LeadSponsorClass]INDUSTRY AND AREA[StudyType]INTERVENTIONAL"
    )
    params = {
        "format": "json",
        "pageSize": str(page_size),
        "countTotal": "true",
        "filter.overallStatus": statuses,
        "filter.advanced": advanced,
        "fields": "NCTId,BriefTitle,Acronym,OverallStatus,Phase,LeadSponsorName,LeadSponsorClass,PrimaryCompletionDate,LastUpdatePostDate,EnrollmentCount,Condition,InterventionName",
    }
    if page_token:
        params["pageToken"] = page_token
    return CTGOV_STUDIES + "?" + urllib.parse.urlencode(params)


def fetch_json(url: str):
    from time import monotonic
    started = monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": "MOZES-Biotech-Catalyst/0.2"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            value = json.loads(r.read().decode("utf-8"))
        record("clinicaltrials.gov", cache=False, duration_ms=(monotonic() - started) * 1000)
        return value
    except Exception:
        record("clinicaltrials.gov", cache=False, error=True, duration_ms=(monotonic() - started) * 1000)
        raise


def study_to_candidate(study: dict, sponsor_map: list[dict] | None = None) -> dict:
    ps = study.get("protocolSection", study)
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    design = ps.get("designModule", {})
    sponsor_mod = ps.get("sponsorCollaboratorsModule", {})
    cond_mod = ps.get("conditionsModule", {})
    arms = ps.get("armsInterventionsModule", {})
    nct = ident.get("nctId") or study.get("NCTId")
    sponsor = (sponsor_mod.get("leadSponsor") or {}).get("name") or study.get("LeadSponsorName") or ""
    pc = (status.get("primaryCompletionDateStruct") or {}).get("date") or study.get("PrimaryCompletionDate")
    last = (status.get("lastUpdatePostDateStruct") or {}).get("date") or study.get("LastUpdatePostDate")
    phases = design.get("phases") or study.get("Phase") or []
    if isinstance(phases, str):
        phases = [phases]
    mapping = best_mapping(sponsor, sponsor_map or [])
    interventions = [x.get("name") for x in arms.get("interventions", []) if x.get("name")]
    return {
        "candidate_id": _candidate_id(nct or "UNKNOWN", pc),
        "nct_id": nct,
        "sponsor": sponsor,
        "ticker": mapping.get("ticker") if mapping else None,
        "ticker_confidence": float(mapping.get("confidence", 0)) if mapping else 0.0,
        "phase": "/".join(phases),
        "title": ident.get("briefTitle") or study.get("BriefTitle"),
        "primary_completion": pc,
        "last_update_posted": last,
        "status": status.get("overallStatus") or study.get("OverallStatus"),
        "conditions": cond_mod.get("conditions") or study.get("Condition") or [],
        "interventions": interventions or study.get("InterventionName") or [],
        "source_type": "clinicaltrials",
        "verification_state": "DISCOVERED",
        "date_semantics": "sponsor-estimated primary completion; NOT a readout date",
        "raw": study,
    }


def discover(start: str | None = None, end: str | None = None, months=6, sponsor_map=None, fetcher=None,
             lookback_days=730, max_pages=None, today=None, budget_seconds=120, return_metadata=False):
    """Discover registry candidates, paginating until exhausted or budgeted.

    ``max_pages`` remains available as an explicit safety cap for callers, but a
    missing cap no longer silently truncates deep discovery.  The returned list
    remains backward compatible; ``return_metadata=True`` returns ``(rows, meta)``.
    """
    from time import monotonic
    started = monotonic()
    fetcher = fetcher or cached_fetch_json
    today = today or date.today()
    start = start or today.isoformat()
    end = end or (today + timedelta(days=31 * months)).isoformat()
    out, seen = [], set()
    windows = [(start, end, ACTIVE)]
    if lookback_days:
        windows.append(((today - timedelta(days=min(lookback_days, 1095))).isoformat(), today.isoformat(),
                        ACTIVE + ",COMPLETED"))
    pages_fetched = candidates_seen = 0
    truncated = False
    stop_reason = "complete"
    next_page_token_present = False
    for lo, hi, statuses in windows:
        token = None
        window_pages = 0
        while True:
            if max_pages is not None and window_pages >= max_pages:
                truncated, stop_reason = True, "page_cap"
                break
            if monotonic() - started >= budget_seconds:
                truncated, stop_reason = True, "time_budget"
                break
            payload = fetcher(query_url(lo, hi, page_size=100, page_token=token, statuses=statuses))
            pages_fetched += 1
            window_pages += 1
            for study in payload.get("studies", []):
                candidates_seen += 1
                candidate = study_to_candidate(study, sponsor_map)
                if statuses != ACTIVE and candidate["status"] == "COMPLETED" and (candidate.get("last_update_posted") or "") < (today - timedelta(days=180)).isoformat():
                    continue
                if candidate["nct_id"] and candidate["nct_id"] not in seen:
                    seen.add(candidate["nct_id"])
                    out.append(candidate)
            token = payload.get("nextPageToken")
            if not token:
                break
        if token:
            next_page_token_present = True
            if not truncated:
                truncated, stop_reason = True, "page_cap"
        if truncated and stop_reason == "time_budget":
            break
    meta = {"pages_fetched": pages_fetched, "candidates_seen": candidates_seen,
            "candidates_kept": len(out), "truncated": truncated,
            "stop_reason": stop_reason, "next_page_token_present": next_page_token_present,
            "duration_ms": round((monotonic() - started) * 1000)}
    return (out, meta) if return_metadata else out


def cached_fetch_json(url):
    """One-day public registry cache; pagination remains bounded by discover()."""
    import os
    import time
    from pathlib import Path
    from .sec_http import _atomic
    root = Path(os.environ.get("MOZES_HTTP_CACHE", ".monitor/http-cache"))
    path = root / ("ctgov-" + hashlib.sha256(url.encode()).hexdigest() + ".json")
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if 0 <= time.time() - cached["at"] < 86400:
            record("clinicaltrials.gov", cache=True)
            return cached["data"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    data = fetch_json(url)
    _atomic(path, json.dumps({"at": time.time(), "data": data}).encode())
    return data


def store_candidates(conn, candidates: list[dict], discovered_at=None):
    discovered_at = discovered_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        for c in candidates:
            # Retain the original row and promotion when registry estimates move.
            prior = conn.execute("SELECT candidate_id,discovered_at FROM discovery_candidates WHERE nct_id=? ORDER BY (promoted_event_id IS NOT NULL) DESC,discovered_at DESC LIMIT 1", (c.get("nct_id"),)).fetchone()
            candidate_id = prior["candidate_id"] if prior else c["candidate_id"]
            first_seen = prior["discovered_at"] if prior else discovered_at
            conn.execute(
                "INSERT INTO discovery_candidates(candidate_id,nct_id,sponsor,ticker,ticker_confidence,phase,title,primary_completion,last_update_posted,status,raw_json,discovered_at,promoted_event_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET sponsor=excluded.sponsor,ticker=excluded.ticker,ticker_confidence=excluded.ticker_confidence,"
                "phase=excluded.phase,title=excluded.title,primary_completion=excluded.primary_completion,last_update_posted=excluded.last_update_posted,status=excluded.status,raw_json=excluded.raw_json,discovered_at=excluded.discovered_at",
                (candidate_id, c.get("nct_id"), c.get("sponsor"), c.get("ticker"), c.get("ticker_confidence"), c.get("phase"), c.get("title"),
                 c.get("primary_completion"), c.get("last_update_posted"), c.get("status"), json.dumps(c.get("raw") or {}, ensure_ascii=False), first_seen, None),
            )
