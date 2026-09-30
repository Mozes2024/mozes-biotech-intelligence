import json
import sqlite3
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
