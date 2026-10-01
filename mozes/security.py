"""Source-provenanced security tradability and asset ownership controls."""
import os

from . import db

NON_TRADABLE = {"ACQUIRED", "DELISTED", "SUSPENDED", "BANKRUPT", "RENAMED"}

def tradability(conn, ticker):
    row = db.security_lifecycle_row(conn, ticker)
    if not row:
        return {"status": "UNKNOWN", "tradable": False, "verified": False, "reason": "no current security lifecycle verification"}
    active = row["status"] == "ACTIVE" and bool(row["source_url"] and row["verified_at"])
    return {**row, "tradable": active, "verified": active}

def status_he(status):
    return {"ACTIVE":"נסחרת", "ACQUIRED":"נרכשה", "DELISTED":"נמחקה מהמסחר", "SUSPENDED":"נמחקה מהמסחר"}.get(status, "סטטוס לא מאומת")

def audit_watch_universe(conn, rows, *, provider="sec"):
    """Apply an official current ticker universe; absent tickers fail safe as UNKNOWN."""
    active = {str(r.get("ticker") or "").upper(): r for r in rows
              if r.get("ticker") and r.get("test_issue", "N") != "Y" and r.get("etf", "N") != "Y"}
    audited = []
    for watch in db.watch_rows(conn):
        ticker = watch["ticker"]
        match = active.get(ticker)
        prior = db.security_lifecycle_row(conn, ticker)
        if prior and prior["status"] in NON_TRADABLE:
            audited.append(prior)
            continue
        if match:
            source_url = match.get("source_url") or "https://www.sec.gov/files/company_tickers_exchange.json"
            db.upsert_security_lifecycle(conn, ticker, company=watch.get("company") or match.get("name"), status="ACTIVE",
                                         source_url=source_url, source_type=f"{provider}_security_master",
                                         reason="present in current official listing directory")
            with conn:
                conn.execute("INSERT INTO security_listing_audit(ticker,exchange,financial_status,source_url,checked_at) VALUES(?,?,?,?,?) "
                             "ON CONFLICT(ticker) DO UPDATE SET exchange=excluded.exchange,financial_status=excluded.financial_status,source_url=excluded.source_url,checked_at=excluded.checked_at",
                             (ticker, match.get("exchange"), match.get("financial_status"), source_url, db.utcnow()))
        else:
            db.upsert_security_lifecycle(conn, ticker, company=watch.get("company"), status="UNKNOWN",
                                         reason="absent from current official listing directory; corporate action unverified")
        audited.append(db.security_lifecycle_row(conn, ticker))
    return audited


def audit_current_universe(conn, provider="auto"):
    if provider == "auto":
        provider = "sec" if os.environ.get("SEC_USER_AGENT") else "nasdaq"
    if provider == "sec":
        from .ingest.edgar import fetch_company_ticker_map
        rows = fetch_company_ticker_map()
    elif provider == "nasdaq":
        from .ingest.nasdaq_trader import fetch_current_listings
        rows = fetch_current_listings()
    else:
        raise ValueError(f"unsupported security audit provider: {provider}")
    audited = audit_watch_universe(conn, rows, provider=provider)
    return {"provider": provider, "audited": len(audited),
            "active": sum(row["status"] == "ACTIVE" for row in audited),
            "unknown": sum(row["status"] == "UNKNOWN" for row in audited),
            "terminal": sum(row["status"] in NON_TRADABLE for row in audited)}
