"""Reconcile persisted watch_universe rows with tickers that no longer trade.

Only the `active` flag changes. Rows are never deleted and no event, checkpoint, change id or
notification history is touched, so history stays readable while polling and alerts stop.
"""
from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).parent / "data" / "retired_tickers.json"


def retired_tickers():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.strip().upper(): v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def reconcile_retired_watches(conn):
    """Deactivate retired tickers; carry the CIK to a successor ticker that lacks one. Idempotent."""
    from . import db
    deactivated, carried = [], []
    retired = retired_tickers()
    for ticker, info in retired.items():
        row = conn.execute("SELECT ticker,company,cik,active FROM watch_universe WHERE ticker=?", (ticker,)).fetchone()
        if not row:
            continue
        successor = (info.get("successor") or "").strip().upper() or None
        if successor and row["cik"]:
            target = conn.execute("SELECT cik,active FROM watch_universe WHERE ticker=?", (successor,)).fetchone()
            if target is None:
                db.upsert_watch(conn, successor, company=row["company"], cik=row["cik"], source="priority_hot_lane")
                carried.append(successor)
            elif not target["cik"]:
                with conn:
                    conn.execute("UPDATE watch_universe SET cik=?,updated_at=? WHERE ticker=?",
                                 (row["cik"], db.utcnow(), successor))
                carried.append(successor)
        if row["active"]:
            with conn:
                conn.execute("UPDATE watch_universe SET active=0,updated_at=? WHERE ticker=?", (db.utcnow(), ticker))
            deactivated.append(ticker)
    return {"deactivated": deactivated, "cik_carried_to": carried}
