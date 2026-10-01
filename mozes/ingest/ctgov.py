"""ClinicalTrials.gov API v2 (free, official). The public API returns the CURRENT record only,
so point-in-time integrity requires storing every fetch as a new version (trial_record_versions)."""
import json
import urllib.request
from datetime import datetime, timedelta, timezone

URL = "https://clinicaltrials.gov/api/v2/studies/{nct}"


def fetch_study(nct):
    from ..discovery import cached_fetch_json
    return cached_fetch_json(URL.format(nct=nct))


def summarize(study):
    ps = study.get("protocolSection", {})
    design = ps.get("designModule", {})
    return {
        "nct_id": ps.get("identificationModule", {}).get("nctId"),
        "title": ps.get("identificationModule", {}).get("briefTitle"),
        "status": ps.get("statusModule", {}).get("overallStatus"),
        "last_update_posted": ps.get("statusModule", {}).get("lastUpdatePostDateStruct", {}).get("date"),
        "primary_completion": ps.get("statusModule", {}).get("primaryCompletionDateStruct", {}),
        "phases": design.get("phases"),
        "enrollment": design.get("enrollmentInfo", {}).get("count"),
        "allocation": design.get("designInfo", {}).get("allocation"),
        "masking": design.get("designInfo", {}).get("maskingInfo", {}).get("masking"),
        "primary_outcomes": [o.get("measure") for o in ps.get("outcomesModule", {}).get("primaryOutcomes", [])],
    }


def store_version(conn, study):
    s = summarize(study)
    retrieved = datetime.now(timezone.utc)
    last = conn.execute("SELECT retrieved_at FROM trial_record_versions WHERE nct_id=? ORDER BY retrieved_at DESC LIMIT 1", (s["nct_id"],)).fetchone()
    if last:
        previous = datetime.fromisoformat(last["retrieved_at"])
        if retrieved <= previous:
            retrieved = previous + timedelta(microseconds=1)
    with conn:
        conn.execute("INSERT INTO trial_record_versions (nct_id, retrieved_at, last_update_posted, payload) VALUES (?,?,?,?)",
                     (s["nct_id"], retrieved.isoformat(timespec="microseconds"),
                      s["last_update_posted"], json.dumps(study)))
    return s
