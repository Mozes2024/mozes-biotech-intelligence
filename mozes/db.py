import json
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def seed(conn, sources, live, historical, outcomes):
    with conn:
        for s in sources.values():
            conn.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?)",
                         (s["id"], s["url"], s.get("source_type"), s.get("reliability"),
                          s.get("published"), s.get("retrieved"), s.get("title")))
        for kind, events in (("live", live), ("historical", historical)):
            for e in events:
                conn.execute("INSERT OR REPLACE INTO events (id, kind, payload) VALUES (?,?,?)",
                             (e["id"], kind, json.dumps(e, ensure_ascii=False)))
        for k, o in outcomes.items():
            conn.execute("INSERT OR REPLACE INTO event_outcomes (event_id, payload) VALUES (?,?)",
                         (k, json.dumps(o, ensure_ascii=False)))


def store_score_run(conn, analysis):
    with conn:
        conn.execute("INSERT INTO score_runs (event_id, as_of, created_at, versions, result) VALUES (?,?,?,?,?)",
                     (analysis["id"], analysis["as_of"], datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      json.dumps(analysis["versions"]), json.dumps(analysis, ensure_ascii=False, default=str)))


def store_statements(conn, statements, retrieved_at):
    with conn:
        for s in statements:
            w = s["window"]
            conn.execute("INSERT INTO extracted_statements (source_id, retrieved_at, statement, catalyst_type, "
                         "date_precision, window_start, window_end, confidence) VALUES (?,?,?,?,?,?,?,?)",
                         (s.get("source_id"), retrieved_at, s["statement"], s["catalyst_type"],
                          w["precision"], w["start"], w["end"], w["confidence"]))


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def count_events(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]


def load_events(conn, kind=None):
    if kind:
        rows = conn.execute("SELECT id, kind, payload FROM events WHERE kind=? ORDER BY id", (kind,)).fetchall()
    else:
        rows = conn.execute("SELECT id, kind, payload FROM events ORDER BY kind,id").fetchall()
    return [json.loads(r["payload"]) for r in rows]


def load_outcomes(conn):
    rows = conn.execute("SELECT event_id,payload FROM event_outcomes").fetchall()
    return {r["event_id"]: json.loads(r["payload"]) for r in rows}


def event_state(conn, event_id):
    r = conn.execute("SELECT * FROM event_state WHERE event_id=?", (event_id,)).fetchone()
    return dict(r) if r else None


def upsert_event_state(conn, event_id, status="CANDIDATE", verification_state="QUARANTINED",
                       verification_confidence=0, event_timestamp=None, event_session="unknown",
                       resolved_at=None, note=None):
    now = utcnow()
    with conn:
        conn.execute(
            "INSERT INTO event_state(event_id,status,verification_state,verification_confidence,event_timestamp,event_session,resolved_at,updated_at,note) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET "
            "status=excluded.status,verification_state=excluded.verification_state,verification_confidence=excluded.verification_confidence,"
            "event_timestamp=COALESCE(excluded.event_timestamp,event_state.event_timestamp),event_session=excluded.event_session,"
            "resolved_at=COALESCE(excluded.resolved_at,event_state.resolved_at),updated_at=excluded.updated_at,note=excluded.note",
            (event_id, status, verification_state, int(verification_confidence), event_timestamp, event_session, resolved_at, now, note),
        )


def add_event_source(conn, event_id, source_id, source_type, url=None, published_at=None,
                     statement=None, supports_date=False, retrieved_at=None):
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO event_sources(event_id,source_id,source_type,url,published_at,statement,supports_date,retrieved_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (event_id, source_id, source_type, url, published_at, statement, int(bool(supports_date)), retrieved_at or utcnow()),
        )


def load_event_sources(conn, event_id):
    return [dict(r) for r in conn.execute("SELECT * FROM event_sources WHERE event_id=? ORDER BY published_at,retrieved_at", (event_id,)).fetchall()]


def store_prices(conn, ticker, rows, source):
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO prices(ticker,date,close,volume,source) VALUES(?,?,?,?,?)",
            [(ticker, r["date"][:10], float(r["close"]), r.get("volume"), source) for r in rows],
        )


def archive_source(conn, source_id, canonical_url, source_type, published_at, retrieved_at, content, metadata=None):
    """Store immutable source bytes; an idempotent re-import must be identical."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = conn.execute("SELECT content_hash FROM source_archive WHERE source_id=?", (source_id,)).fetchone()
    if existing:
        if existing["content_hash"] != digest:
            raise ValueError(f"source_id {source_id} was already archived with different content")
        return digest
    with conn:
        conn.execute("INSERT INTO source_archive(source_id,canonical_url,source_type,published_at,retrieved_at,content_hash,content,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                     (source_id, canonical_url, source_type, published_at, retrieved_at, digest, content,
                      json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)))
    return digest


def source_archive_row(conn, source_id):
    row = conn.execute("SELECT * FROM source_archive WHERE source_id=?", (source_id,)).fetchone()
    return dict(row) if row else None


def store_price_ingestion_run(conn, run_id, provider, metadata=None):
    existing = conn.execute("SELECT provider,metadata_json FROM price_ingestion_runs WHERE run_id=?", (run_id,)).fetchone()
    payload = json.dumps(metadata or {}, sort_keys=True)
    if existing:
        if existing["provider"] != provider or existing["metadata_json"] != payload:
            raise ValueError(f"price run {run_id} already exists with different provenance")
        return
    with conn:
        conn.execute("INSERT INTO price_ingestion_runs(run_id,provider,requested_at,metadata_json) VALUES(?,?,?,?)", (run_id, provider, utcnow(), payload))


def attach_historical_prices(conn, case_id, ticker, start_date, end_date, provider, run_id=None, benchmark="XBI"):
    with conn:
        conn.execute("INSERT INTO historical_price_attachments(case_id,ticker,benchmark,start_date,end_date,provider,run_id,attached_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(case_id,ticker,benchmark) DO UPDATE SET start_date=excluded.start_date,end_date=excluded.end_date,provider=excluded.provider,run_id=excluded.run_id,attached_at=excluded.attached_at",
                     (case_id, ticker, benchmark, start_date, end_date, provider, run_id, utcnow()))
        conn.execute("DELETE FROM historical_case_prices WHERE case_id=?", (case_id,))
        for symbol in {ticker, benchmark}:
            conn.execute("INSERT INTO historical_case_prices(case_id,ticker,date,close,volume,source) "
                         "SELECT ?,ticker,date,close,volume,source FROM prices "
                         "WHERE ticker=? AND date BETWEEN ? AND ? AND source=?",
                         (case_id, symbol, start_date, end_date, provider))


def load_prices(conn, ticker, before=None, after=None):
    sql = "SELECT date,close,volume,source FROM prices WHERE ticker=?"
    args = [ticker]
    if before:
        sql += " AND date < ?"
        args.append(before)
    if after:
        sql += " AND date >= ?"
        args.append(after)
    sql += " ORDER BY date"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def validation_row(conn, name):
    r = conn.execute("SELECT * FROM model_validation WHERE model_name=?", (name,)).fetchone()
    return dict(r) if r else None


def set_validation(conn, name, enabled, oos_n, min_oos_n, metrics=None, note=None):
    with conn:
        conn.execute(
            "INSERT INTO model_validation(model_name,enabled,oos_n,min_oos_n,metrics_json,note,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(model_name) DO UPDATE SET enabled=excluded.enabled,oos_n=excluded.oos_n,min_oos_n=excluded.min_oos_n,"
            "metrics_json=excluded.metrics_json,note=excluded.note,updated_at=excluded.updated_at",
            (name, int(bool(enabled)), int(oos_n), int(min_oos_n), json.dumps(metrics or {}), note, utcnow()),
        )


def upsert_watch(conn, ticker, company=None, cik=None, source="manual", active=True):
    with conn:
        conn.execute(
            "INSERT INTO watch_universe(ticker,company,cik,source,active,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET company=COALESCE(excluded.company,watch_universe.company),cik=COALESCE(excluded.cik,watch_universe.cik),source=excluded.source,active=excluded.active,updated_at=excluded.updated_at",
            (ticker.upper(), company, cik, source, int(bool(active)), utcnow()),
        )


def watch_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM watch_universe WHERE active=1 ORDER BY ticker").fetchall()]


def upsert_security_lifecycle(conn, ticker, *, company=None, status="UNKNOWN", effective_from=None, effective_to=None,
                              successor_ticker=None, acquirer_ticker=None, reason=None, source_url=None, source_type=None,
                              published_at=None, verified_at=None):
    ticker = ticker.upper()
    row = conn.execute("SELECT * FROM security_lifecycle WHERE ticker=?", (ticker,)).fetchone()
    values = (ticker, company, status, effective_from, effective_to, successor_ticker, acquirer_ticker, reason, source_url, source_type, published_at, verified_at or utcnow())
    if row:
        old = tuple(row[k] for k in ("ticker","company","status","effective_from","effective_to","successor_ticker","acquirer_ticker","reason","source_url","source_type","published_at"))
        if old == values[:-1]: return
    with conn:
        conn.execute("INSERT INTO security_lifecycle(ticker,company,status,effective_from,effective_to,successor_ticker,acquirer_ticker,reason,source_url,source_type,published_at,verified_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET company=excluded.company,status=excluded.status,effective_from=excluded.effective_from,effective_to=excluded.effective_to,successor_ticker=excluded.successor_ticker,acquirer_ticker=excluded.acquirer_ticker,reason=excluded.reason,source_url=excluded.source_url,source_type=excluded.source_type,published_at=excluded.published_at,verified_at=excluded.verified_at", values)


def security_lifecycle_row(conn, ticker):
    row = conn.execute("SELECT * FROM security_lifecycle WHERE ticker=?", ((ticker or "").upper(),)).fetchone()
    return dict(row) if row else None


def upsert_asset_ownership(conn, asset_id, owner_ticker, effective_from, *, relationship, source_url, source_type, published_at=None, effective_to=None):
    with conn:
        conn.execute("INSERT INTO asset_ownership(asset_id,owner_ticker,effective_from,effective_to,relationship,source_url,source_type,published_at,verified_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(asset_id,owner_ticker,effective_from) DO UPDATE SET effective_to=excluded.effective_to,relationship=excluded.relationship,source_url=excluded.source_url,source_type=excluded.source_type,published_at=excluded.published_at,verified_at=excluded.verified_at", (asset_id, owner_ticker.upper(), effective_from, effective_to, relationship, source_url, source_type, published_at, utcnow()))


def upsert_historical_case(conn, case_id, ticker, catalyst_type, event_at, *, legacy_event_id=None,
                           announcement_session="unknown", provenance=None, legacy_post_hoc=False):
    with conn:
        conn.execute(
            "INSERT INTO historical_cases(case_id,legacy_event_id,ticker,catalyst_type,event_at,announcement_session,provenance_json,legacy_post_hoc,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET ticker=excluded.ticker,catalyst_type=excluded.catalyst_type,event_at=excluded.event_at,announcement_session=excluded.announcement_session,provenance_json=excluded.provenance_json,legacy_post_hoc=excluded.legacy_post_hoc",
            (case_id, legacy_event_id, ticker, catalyst_type, event_at, announcement_session, json.dumps(provenance or [], ensure_ascii=False), int(bool(legacy_post_hoc)), utcnow()),
        )


def store_feature_snapshot(conn, snapshot_id, case_id, as_of, payload, *, provenance=None, blinded=False):
    existing = conn.execute("SELECT case_id,as_of,payload,provenance_json,blinded FROM feature_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    prov = json.dumps(provenance or [], ensure_ascii=False, sort_keys=True)
    if existing:
        if (existing["case_id"], existing["as_of"], existing["payload"], existing["provenance_json"], existing["blinded"]) != (case_id, as_of, encoded, prov, int(bool(blinded))):
            raise ValueError(f"snapshot {snapshot_id} already exists with different contents")
        return
    with conn:
        conn.execute(
            "INSERT INTO feature_snapshots(snapshot_id,case_id,as_of,payload,provenance_json,blinded,created_at) VALUES(?,?,?,?,?,?,?)",
            (snapshot_id, case_id, as_of, encoded, prov, int(bool(blinded)), utcnow()),
        )


def store_outcome_label(conn, label_id, case_id, labeled_at, payload, *, provenance=None, verified=False):
    existing = conn.execute("SELECT case_id,labeled_at,payload,provenance_json,verified FROM outcome_labels WHERE label_id=?", (label_id,)).fetchone()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    prov = json.dumps(provenance or [], ensure_ascii=False, sort_keys=True)
    if existing:
        if (existing["case_id"], existing["labeled_at"], existing["payload"], existing["provenance_json"], existing["verified"]) != (case_id, labeled_at, encoded, prov, int(bool(verified))):
            raise ValueError(f"outcome label {label_id} already exists with different contents")
        return
    with conn:
        conn.execute(
            "INSERT INTO outcome_labels(label_id,case_id,labeled_at,payload,provenance_json,verified,created_at) VALUES(?,?,?,?,?,?,?)",
            (label_id, case_id, labeled_at, encoded, prov, int(bool(verified)), utcnow()),
        )


def historical_case_rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM historical_cases ORDER BY event_at,case_id").fetchall()]


def feature_snapshot_rows(conn, case_id):
    return [dict(r) for r in conn.execute("SELECT * FROM feature_snapshots WHERE case_id=? ORDER BY as_of", (case_id,)).fetchall()]


def outcome_label_rows(conn, case_id):
    return [dict(r) for r in conn.execute("SELECT * FROM outcome_labels WHERE case_id=? ORDER BY labeled_at", (case_id,)).fetchall()]
