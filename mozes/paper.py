"""Paper mode: append-only forward-test ledger. Signals can never be edited or deleted
(enforced by SQLite triggers). Outcomes are write-once and cannot precede the catalyst window."""
import json
from datetime import datetime, timezone


class PaperBook:
    def __init__(self, conn):
        self.conn = conn

    def record(self, analysis, price=None, now=None):
        now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
        sid = f"{analysis['id']}__{now}"
        w = (analysis.get("date") or {}).get("window") or {}
        classification = analysis.get("classification") or {}
        class_name = classification.get("class") or classification.get("cls") or "WATCH"
        versions = analysis.get("versions") or {"product": "0.3.0", "evidence_engine": (analysis.get("evidence") or {}).get("engine")}
        with self.conn:
            self.conn.execute(
                "INSERT INTO paper_signals (signal_id, event_id, created_at, as_of, window_start, window_end, "
                "price, classification, versions, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (sid, analysis["id"], now, analysis.get("as_of", ""), w.get("start"), w.get("end"), price,
                 class_name, json.dumps(versions),
                 json.dumps(analysis, ensure_ascii=False, default=str)))
        return sid

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
