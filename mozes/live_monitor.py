"""Point-in-time, source-provenanced changes in the current watch universe.

Registry observations are investigation signals, never catalyst verification.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone

from . import db
from .ingest import ctgov, edgar

CT_FIELDS = {
    "status": "ctgov_status_changed",
    "primary_completion": "ctgov_primary_completion_changed",
    "primary_completion_type": "ctgov_primary_completion_type_changed",
    "enrollment": "ctgov_enrollment_changed",
    "why_stopped": "ctgov_why_stopped_changed",
    "study_completion": "ctgov_study_completion_changed",
    "last_update_posted": "ctgov_last_update_changed",
    "results_first_posted": "ctgov_results_first_posted_changed",
}
FINANCING_FORMS = {"424B5", "424B4", "S-3", "S-1"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def record_change(conn, *, ticker, change_type, previous_value, new_value, source_url,
                  source_type, severity="medium", verification_state="unverified",
                  event_id=None, candidate_id=None, nct_id=None, asset=None,
                  source_hash=None, metadata=None, identity=None):
    """Stable identity includes severity, so a later escalation is retained."""
    key = identity or [ticker, event_id, candidate_id, nct_id, change_type,
                       previous_value, new_value, source_url, source_hash]
    change_id = "CHG-" + _hash([key, severity])[:24]
    with conn:
        inserted = conn.execute(
            "INSERT OR IGNORE INTO change_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (change_id, _now(), ticker, event_id, candidate_id, nct_id, asset,
             change_type, _json(previous_value), _json(new_value), severity,
             source_url, source_type, verification_state, source_hash,
             _json(metadata or {})),
        )
    if inserted.rowcount:
        try:
            from .alert_dispatch import enqueue_from_change_row
            enqueue_from_change_row(conn, change_id=change_id, change_type=change_type, severity=severity)
        except Exception:
            # Delivery must never roll back detection; outbox failures are retried later.
            pass
    return change_id


def observe(conn, key, value, *, source_url=None, source_type=None):
    digest = _hash(value)
    old = conn.execute("SELECT value_json,content_hash FROM monitor_observations WHERE observation_key=?", (key,)).fetchone()
    if old and old["content_hash"] == digest:
        return None
    with conn:
        conn.execute("INSERT INTO monitor_observations VALUES (?,?,?,?,?,?) "
                     "ON CONFLICT(observation_key) DO UPDATE SET value_json=excluded.value_json,content_hash=excluded.content_hash,source_url=excluded.source_url,source_type=excluded.source_type,observed_at=excluded.observed_at",
                     (key, _json(value), digest, source_url, source_type, db.utcnow()))
    return json.loads(old["value_json"]) if old else None


def _study_fields(study):
    ps = study.get("protocolSection") or {}
    status = ps.get("statusModule") or {}
    design = ps.get("designModule") or {}
    pc = status.get("primaryCompletionDateStruct") or {}
    return {
        "status": status.get("overallStatus"),
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "primary_completion": pc.get("date"),
        # ESTIMATED → ACTUAL is an early imminence signal; still never a readout date.
        "primary_completion_type": pc.get("type"),
        "study_completion": (status.get("completionDateStruct") or {}).get("date"),
        "last_update_posted": (status.get("lastUpdatePostDateStruct") or {}).get("date"),
        "results_first_posted": (status.get("resultsFirstPostDateStruct") or {}).get("date"),
        "why_stopped": status.get("whyStopped"),
    }


def monitor_study(conn, study, *, ticker=None, candidate_id=None):
    nct = (study.get("protocolSection") or {}).get("identificationModule", {}).get("nctId")
    if not nct:
        raise ValueError("ClinicalTrials.gov record missing NCT ID")
    old = conn.execute("SELECT payload FROM trial_record_versions WHERE nct_id=? ORDER BY retrieved_at DESC LIMIT 1", (nct,)).fetchone()
    fields = _study_fields(study)
    digest = _hash(study)
    if old and _hash(json.loads(old["payload"])) == digest:
        return []
    prior = _study_fields(json.loads(old["payload"])) if old else None
    # A version is evidence of what the registry returned at retrieval time only.
    ctgov.store_version(conn, study)
    if prior is None:
        return []
    source_url = ctgov.URL.format(nct=nct)
    changes = []
    for field, change_type in CT_FIELDS.items():
        if prior[field] != fields[field]:
            changes.append(record_change(
                conn, ticker=ticker, candidate_id=candidate_id, nct_id=nct,
                change_type=change_type, previous_value=prior[field], new_value=fields[field],
                source_url=source_url, source_type="clinicaltrials", source_hash=digest,
                verification_state="investigation_only",
                severity="medium" if field in {
                    "status", "primary_completion", "primary_completion_type",
                    "why_stopped", "results_first_posted"} else "low",
                metadata={"field": field, "last_update_posted": fields["last_update_posted"],
                          "primary_completion_type": fields.get("primary_completion_type"),
                          "not_a_readout_date": True},
            ))
    return changes


def classify_filing(filing, text=""):
    form = filing["form"].upper()
    lowered = text.lower()
    if form in {"S-3", "S-1"}:
        return "shelf/registration only"
    if form in {"424B5", "424B4"}:
        return "potential financing/dilution"
    if form in {"8-K", "6-K"}:
        if any(x in lowered for x in ("closed its public offering", "completed its public offering", "closed the offering")):
            return "completed financing"
        if any(x in lowered for x in ("public offering", "private placement", "securities purchase agreement", "registered direct offering")):
            return "potential financing/dilution"
    return "unknown/review required"


def monitor_filing(conn, ticker, filing, *, text=""):
    form = filing["form"].upper()
    classification = classify_filing(filing, text)
    generic_report = form in {"8-K", "6-K"} and classification == "unknown/review required"
    if form not in FINANCING_FORMS and not generic_report:
        return None
    change_type = "sec_filing_signal" if generic_report else "sec_financing_filing"
    identity = ["sec", filing.get("accession") or filing["url"], ticker, form]
    return record_change(
        conn, ticker=ticker, change_type=change_type, previous_value=None,
        new_value={"form": form, "classification": classification, "filed": filing.get("filed")},
        source_url=filing["url"], source_type="sec", source_hash=_hash([filing, text]),
        verification_state="investigation_only" if generic_report else "primary_source",
        severity="medium" if classification == "completed financing" else "low",
        metadata={"classification": classification, "accepted": filing.get("accepted"),
                  "filed": filing.get("filed"), "accession": filing.get("accession")}, identity=identity,
    )


def reconcile_states(conn, *, today=None):
    """Audit changed state without modifying event evidence, dates, or scores."""
    from .engine_v2 import score_event
    today = today or date.today()
    changes = []
    for event in db.load_events(conn, "live"):
        result = score_event(conn, event, today.isoformat())
        event_id = event["id"]
        state = result.get("state") or {}
        sources = result.get("sources") or []
        source = next((s for s in reversed(sources) if s.get("url")), None)
        source_url = source.get("url") if source else None
        source_type = source.get("source_type") if source else None
        current = {
            "date": (result.get("date") or {}).get("window"),
            "verified": state.get("verification_state") == "VERIFIED",
            "resolved": state.get("status") in {"RESOLVED_SUCCESS", "RESOLVED_FAIL", "RESOLVED_MIXED", "APPROVED", "CRL", "WITHDRAWN", "CANCELLED", "SUPERSEDED"},
            "stale": (result.get("classification") or {}).get("class") == "STALE_UNRESOLVED",
            "recommendation": (result.get("recommendation") or {}).get("status"),
            "security": (result.get("security") or {}).get("status"),
            "review_state": state.get("verification_state"),
        }
        prior = observe(conn, "event:" + event_id, current, source_url=source_url, source_type=source_type)
        if prior is None:
            continue
        mapping = {"date": "catalyst_date_changed", "verified": "catalyst_verified",
                   "resolved": "catalyst_resolved", "stale": "catalyst_became_stale",
                   "recommendation": "recommendation_changed", "security": "security_status_changed",
                   "review_state": "review_state_changed"}
        for field, change_type in mapping.items():
            if prior.get(field) == current[field] or (field in {"verified", "resolved", "stale"} and not current[field]):
                continue
            primary = field in {"date", "verified", "resolved"} and bool(source_url and source_type in {"sec", "fda", "company_ir"})
            changes.append(record_change(conn, ticker=event.get("ticker"), event_id=event_id,
                                         asset=event.get("program"), change_type=change_type,
                                         previous_value=prior.get(field), new_value=current[field],
                                         source_url=source_url if primary else None,
                                         source_type=source_type if primary else "internal_reconciliation",
                                         verification_state="primary_source" if primary else "derived",
                                         severity="medium" if field in {"resolved", "security", "recommendation"} else "low",
                                         metadata={"reason": (result.get("recommendation") or {}).get("why_he") if field == "recommendation" else state.get("note"), "as_of": today.isoformat()}))
    return changes


def recent_changes(conn, limit=100, *, days=7, change_types=None, now=None):
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    sql = "SELECT * FROM change_events WHERE detected_at>=?"
    args = [cutoff]
    if change_types:
        sql += " AND change_type IN (" + ",".join("?" for _ in change_types) + ")"
        args.extend(change_types)
    sql += " ORDER BY detected_at DESC,change_id DESC LIMIT ?"
    args.append(limit)
    return [{**dict(r), "previous_value": json.loads(r["previous_value"]),
             "new_value": json.loads(r["new_value"]), "metadata": json.loads(r["metadata_json"])}
            for r in conn.execute(sql, args)]


from .source_observability import observed, snapshot


@observed
def run_monitor(conn, *, audit=True, ctgov_diff=True, sec=True, filings_per_company=12, news=False,
                priority_only=False):
    from .security import audit_current_universe, audit_watch_universe, NON_TRADABLE
    started = _now()
    with conn:
        rid = conn.execute("INSERT INTO monitor_runs(started_at,status) VALUES(?,'RUNNING')", (started,)).lastrowid
    details = {"studies": 0, "filings": 0, "changes": 0, "errors": []}
    try:
        if audit:
            if os.environ.get("SEC_USER_AGENT"):
                listings = edgar.fetch_company_ticker_map()
                by_ticker = {str(row.get("ticker") or "").upper(): row for row in listings}
                with conn:
                    for watch in db.watch_rows(conn):
                        match = by_ticker.get(watch["ticker"])
                        if match and (match.get("cik") or match.get("cik_str")):
                            conn.execute("UPDATE watch_universe SET cik=? WHERE ticker=?",
                                         (str(match.get("cik") or match.get("cik_str")), watch["ticker"]))
                audited = audit_watch_universe(conn, listings, provider="sec")
                details["security_audit"] = {
                    "provider": "sec", "audited": len(audited),
                    "active": sum(row["status"] == "ACTIVE" for row in audited),
                    "unknown": sum(row["status"] == "UNKNOWN" for row in audited),
                    "terminal": sum(row["status"] in NON_TRADABLE for row in audited),
                }
            else:
                details["security_audit"] = audit_current_universe(conn)
        if ctgov_diff:
            if conn.execute("SELECT COUNT(*) FROM discovery_candidates").fetchone()[0] == 0:
                from .refresh import discover_registry
                details["ctgov_seeded"] = len(discover_registry(conn, months=9))
            studies = conn.execute(
                "SELECT d.nct_id,MAX(d.candidate_id) candidate_id,MAX(d.ticker) ticker "
                "FROM discovery_candidates d "
                "LEFT JOIN monitor_observations m ON m.observation_key='ct_poll:'||d.nct_id "
                "WHERE d.nct_id IS NOT NULL AND d.primary_completion>=? AND d.primary_completion<=? "
                "GROUP BY d.nct_id ORDER BY COALESCE(m.observed_at,'') ASC,d.nct_id LIMIT 100",
                ((date.today() - timedelta(days=90)).isoformat(), (date.today() + timedelta(days=270)).isoformat()),
            ).fetchall()
            for row in studies:
                try:
                    details["changes"] += len(monitor_study(conn, ctgov.fetch_study(row["nct_id"]), ticker=row["ticker"], candidate_id=row["candidate_id"]))
                    details["studies"] += 1
                    observe(conn, "ct_poll:" + row["nct_id"], _now(), source_type="clinicaltrials")
                except Exception as exc:
                    details["errors"].append({"nct": row["nct_id"], "error": str(exc)})
        if sec and os.environ.get("SEC_USER_AGENT"):
            from .priority import priority_tickers
            priority = {ticker: index for index, ticker in enumerate(priority_tickers())}
            watches = sorted(db.watch_rows(conn), key=lambda row: (priority.get(row["ticker"], len(priority)), row["ticker"]))
            if priority_only:
                watches = [row for row in watches if row["ticker"] in priority]
            for watch in watches:
                if not watch.get("cik"):
                    continue
                try:
                    filings = edgar.recent_filings_v2(watch["cik"], forms=("424B5", "424B4", "S-3", "S-1", "8-K", "6-K"), limit=filings_per_company)
                    for filing in filings:
                        if filing.get("filed", "") < (date.today() - timedelta(days=30)).isoformat():
                            continue
                        if filing["form"] in FINANCING_FORMS:
                            monitor_filing(conn, watch["ticker"], filing)
                            details["filings"] += 1
                        elif filing.get("filed", "") >= (date.today() - timedelta(days=14)).isoformat():
                            try:
                                text = edgar.html_to_text(edgar._get(filing["url"]))
                            except Exception as exc:
                                details["errors"].append({"ticker": watch["ticker"], "filing": filing["url"],
                                                          "error": str(exc)[:160]})
                                text = ""
                            if monitor_filing(conn, watch["ticker"], filing, text=text):
                                details["filings"] += 1
                except Exception as exc:
                    details["errors"].append({"ticker": watch["ticker"], "error": str(exc)})
        if news:
            from .news_signals import poll_news
            details["news"] = poll_news(conn, limit=2 if priority_only else 8)
        if os.environ.get("MOZES_PRIMARY_FEEDS", "0") == "1":
            from .primary_feeds import poll_fda_feeds, poll_nasdaq_halts, poll_wire_feeds
            from .priority import priority_tickers as _priority_tickers
            import urllib.request

            def _feed_fetch(url):
                with urllib.request.urlopen(urllib.request.Request(
                        url, headers={"User-Agent": "MozesBiotechMonitor/1.0"}), timeout=8) as response:
                    return response.read(4_000_000)

            details["primary_feeds"] = {
                "fda": poll_fda_feeds(conn, fetch=_feed_fetch),
                "wires": poll_wire_feeds(conn, fetch=_feed_fetch) if not priority_only else {"skipped": True},
                "nasdaq_halts": poll_nasdaq_halts(
                    conn, fetch=_feed_fetch,
                    watch_tickers=[row["ticker"] for row in db.watch_rows(conn)] or list(_priority_tickers())),
            }
        reconcile_states(conn)
        details["changes"] = conn.execute("SELECT COUNT(*) FROM change_events WHERE detected_at>=?", (started,)).fetchone()[0]
        if os.environ.get("MOZES_ALERT_DISPATCH", "0") == "1":
            from .alert_dispatch import sync_new_changes
            details["alerts"] = sync_new_changes(conn, since_iso=started)
        if sec and not os.environ.get("SEC_USER_AGENT"):
            details["sec_skipped"] = "SEC_USER_AGENT not configured"
        status = "OK" if not details["errors"] else "PARTIAL"
    except Exception as exc:
        details["errors"].append({"fatal": str(exc)})
        status = "FAILED"
    details['source_operations'] = snapshot()
    if status == 'OK' and any(row['errors'] for row in details['source_operations']['sources'].values()):
        status = 'PARTIAL'
    with conn:
        conn.execute("UPDATE monitor_runs SET finished_at=?,status=?,details_json=? WHERE run_id=?",
                     (_now(), status, _json(details), rid))
    return {"run_id": rid, "status": status, **details}
