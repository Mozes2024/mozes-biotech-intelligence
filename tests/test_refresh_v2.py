import json

from mozes import db
from mozes.radar import bootstrap_database
from mozes import refresh


def test_sec_first_regulatory_discovery_creates_verified_event(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    db.upsert_watch(conn, "TEST", "Test Bio", cik="123", source="test")
    monkeypatch.setattr(refresh, "recent_filings_v2", lambda cik, limit=10: [{"url":"u","filed":"2026-09-01"}])
    monkeypatch.setattr(refresh, "extract_from_filing_v2", lambda filing: [{
        "catalyst_type":"PDUFA","source_id":"https://sec.test/ex99.htm","filed":"2026-09-01",
        "statement":"FDA assigned a PDUFA target action date of December 15, 2026.",
        "window":{"precision":"exact","start":"2026-12-15","end":"2026-12-15","original":"December 15, 2026"}
    }])
    r = refresh.scan_watch_universe_regulatory(conn)
    assert r["events"]
    eid = r["events"][0]
    st = db.event_state(conn, eid)
    assert st["status"] == "SCHEDULED"
    assert st["verification_state"] == "VERIFIED"


def test_sec_first_does_not_auto_create_clinical_readout_without_candidate(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    db.upsert_watch(conn, "TEST", "Test Bio", cik="123", source="test")
    monkeypatch.setattr(refresh, "recent_filings_v2", lambda cik, limit=10: [{"url":"u","filed":"2026-09-01"}])
    monkeypatch.setattr(refresh, "extract_from_filing_v2", lambda filing: [{
        "catalyst_type":"P3_TOPLINE","source_id":"https://sec.test/ex99.htm","filed":"2026-09-01",
        "statement":"Phase 3 topline is expected in December 2026.",
        "window":{"precision":"month","start":"2026-12-01","end":"2026-12-31","original":"December 2026"}
    }])
    r = refresh.scan_watch_universe_regulatory(conn)
    assert r["events"] == []
