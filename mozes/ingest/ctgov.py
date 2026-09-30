"""ClinicalTrials.gov API v2 (free, official). The public API returns the CURRENT record only,
so point-in-time integrity requires storing every fetch as a new version (trial_record_versions)."""
import json
import urllib.request
from datetime import datetime, timezone

URL = "https://clinicaltrials.gov/api/v2/studies/{nct}"


def fetch_study(nct):
    with urllib.request.urlopen(URL.format(nct=nct), timeout=30) as r:
        return json.loads(r.read().decode())


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
    with conn:
        conn.execute("INSERT INTO trial_record_versions (nct_id, retrieved_at, last_update_posted, payload) VALUES (?,?,?,?)",
                     (s["nct_id"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      s["last_update_posted"], json.dumps(study)))
    return s
