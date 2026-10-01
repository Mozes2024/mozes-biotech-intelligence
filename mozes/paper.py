"""Paper mode: append-only forward-test ledger. Signals can never be edited or deleted
(enforced by SQLite triggers). Outcomes are write-once and cannot precede the catalyst window."""
import json
import hashlib
from datetime import datetime, timezone


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class PaperBook:
    def __init__(self, conn):
        self.conn = conn

    def record(self, analysis, price=None, now=None, idempotency_key=None):
        now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        sid = f"{analysis['id']}__{now}"
        w = (analysis.get("date") or {}).get("window") or {}
        classification = analysis.get("classification") or {}
        class_name = classification.get("class") or classification.get("cls") or "WATCH"
        versions = analysis.get("versions") or {"product": "0.3.0", "evidence_engine": (analysis.get("evidence") or {}).get("engine")}
        with self.conn:
            # Serialize read-previous/append together, including concurrent API writers.
            if not self.conn.in_transaction:
                self.conn.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                old = self.conn.execute("SELECT signal_id,input_hash FROM paper_audit WHERE idempotency_key=?", (idempotency_key,)).fetchone()
                if old:
                    if old["input_hash"] != digest([analysis, price]):
                        raise ValueError("idempotency key reused with different inputs")
                    return old["signal_id"]
            self.conn.execute(
                "INSERT INTO paper_signals (signal_id, event_id, created_at, as_of, window_start, window_end, "
                "price, classification, versions, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, analysis["id"], now, analysis.get("as_of", ""), w.get("start"), w.get("end"), price,
                 class_name, json.dumps(versions),
                 json.dumps(analysis, ensure_ascii=False, default=str)))
            previous = self.conn.execute("SELECT record_hash FROM paper_audit ORDER BY sequence DESC LIMIT 1").fetchone()
            previous_hash = previous["record_hash"] if previous else ""
            source_hash = digest(analysis.get("sources") or [])
            input_hash = digest([analysis, price])
            metadata = {"schema": 1, "signal_id": sid, "event_id": analysis["id"],
                        "as_of": analysis.get("as_of"), "recorded_at": now, "versions": versions,
                        "source_hash": source_hash, "input_hash": input_hash, "authoritative": False,
                        "previous_hash": previous_hash}
            self.conn.execute("INSERT INTO paper_audit(signal_id,idempotency_key,recorded_at,source_hash,input_hash,previous_hash,record_hash,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                              (sid, idempotency_key or sid, now, source_hash, input_hash, previous_hash,
                               digest(metadata), json.dumps(metadata, sort_keys=True)))
        return sid

    def verify(self):
        previous, failures = "", []
        for row in self.conn.execute("SELECT a.*,s.payload,s.price,s.event_id,s.as_of,s.created_at,s.versions FROM paper_audit a LEFT JOIN paper_signals s USING(signal_id) ORDER BY sequence"):
            try:
                metadata = json.loads(row["metadata_json"])
                payload = json.loads(row["payload"])
                valid = (row["previous_hash"] == previous and digest(metadata) == row["record_hash"]
                         and metadata["previous_hash"] == previous
                         and digest([payload, row["price"]]) == row["input_hash"] == metadata["input_hash"]
                         and digest(payload.get("sources") or []) == row["source_hash"] == metadata["source_hash"]
                         and metadata["signal_id"] == row["signal_id"]
                         and metadata["event_id"] == row["event_id"]
                         and (metadata["as_of"] or "") == row["as_of"]
                         and metadata["recorded_at"] == row["recorded_at"] == row["created_at"]
                         and metadata["versions"] == json.loads(row["versions"]))
            except (ValueError, TypeError, KeyError):
                valid = False
            if not valid:
                failures.append(row["signal_id"])
            previous = row["record_hash"]
        legacy = self.conn.execute("SELECT COUNT(*) FROM paper_signals s LEFT JOIN paper_audit a USING(signal_id) WHERE a.signal_id IS NULL").fetchone()[0]
        return {"valid": not failures, "failures": failures, "legacy_unhashed_count": legacy,
                "authoritative": False}

    def queue_candidates(self, analyses, now=None):
        """Fixed research threshold; first qualifying snapshot is retained forever."""
        count = 0
        with self.conn:
            for row in analyses:
                if ((row.get("state") or {}).get("verification_state") != "VERIFIED"
                        or (row.get("classification") or {}).get("class") not in {"REVIEW", "WATCH"}
                        or (row.get("evidence") or {}).get("score", 0) is None
                        or (row.get("evidence") or {}).get("score", 0) < 65
                        or (row.get("impact") or {}).get("score", 0) < 65):
                    continue
                count += self.conn.execute("INSERT OR IGNORE INTO forward_candidates VALUES(?,?,?,?)",
                    (row["id"] + ":research65-v1", row["id"], now or datetime.now(timezone.utc).isoformat(),
                     json.dumps(row, ensure_ascii=False, default=str))).rowcount
        return count

    def attach_outcome(self, signal_id, clinical, move, today, note=""):
        row = self.conn.execute("SELECT window_start FROM paper_signals WHERE signal_id=?", (signal_id,)).fetchone()
        if row is None:
            raise KeyError(signal_id)
        if row[0] and today < row[0]:
            raise ValueError("cannot attach an outcome before the catalyst window opens")
        with self.conn:
            self.conn.execute("INSERT INTO paper_outcomes (signal_id, attached_at, clinical, move, note) VALUES (?,?,?,?,?)",
                              (signal_id, today, clinical, move, note))

    def list(self):
        rows = self.conn.execute(
            "SELECT s.signal_id, s.event_id, s.created_at, s.classification, s.window_start, s.price, "
            "o.clinical, o.move FROM paper_signals s LEFT JOIN paper_outcomes o USING (signal_id) "
            "ORDER BY s.created_at").fetchall()
        return [dict(r) for r in rows]
