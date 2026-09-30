"""Source-provenanced security tradability and asset ownership controls."""
from . import db

NON_TRADABLE = {"ACQUIRED", "DELISTED", "SUSPENDED", "BANKRUPT"}

def tradability(conn, ticker):
    row = db.security_lifecycle_row(conn, ticker)
    if not row:
        return {"status": "UNKNOWN", "tradable": False, "verified": False, "reason": "no current security lifecycle verification"}
    active = row["status"] == "ACTIVE" and bool(row["source_url"] and row["verified_at"])
    return {**row, "tradable": active, "verified": active}

def status_he(status):
    return {"ACTIVE":"נסחרת", "ACQUIRED":"נרכשה", "DELISTED":"נמחקה מהמסחר", "SUSPENDED":"נמחקה מהמסחר"}.get(status, "סטטוס לא מאומת")

def audit_watch_universe(conn, rows):
    """Apply an official current ticker universe; absent tickers fail safe as UNKNOWN."""
    active = {str(r.get("ticker") or "").upper(): r for r in rows}
    audited = []
    for watch in db.watch_rows(conn):
        ticker = watch["ticker"]
        match = active.get(ticker)
        if match:
            db.upsert_security_lifecycle(conn, ticker, company=match.get("name") or watch.get("company"), status="ACTIVE", source_url="https://www.sec.gov/files/company_tickers_exchange.json", source_type="sec_security_master", reason="present in current SEC ticker universe")
        elif not db.security_lifecycle_row(conn, ticker):
            db.upsert_security_lifecycle(conn, ticker, company=watch.get("company"), status="UNKNOWN", reason="not verified by current official universe audit")
        audited.append(db.security_lifecycle_row(conn, ticker))
    return audited
