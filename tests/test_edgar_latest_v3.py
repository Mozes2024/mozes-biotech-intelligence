"""Offline replay of a captured SEC Latest Filings Atom response from 2026-10-08."""
import json
from pathlib import Path

from mozes import db
from mozes.ingest import edgar_latest


FIXTURE = (Path(__file__).parent / "fixtures" / "sec_latest_8k.atom").read_bytes()


def _conn(tmp_path, cik):
    conn = db.connect(tmp_path / "sec.db")
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("captured", "Captured Bio", "ZZZZ", cik, .99, "SEC-v2C-equity", "2026-10-08"))
    db.upsert_watch(conn, "ZZZZ", company="Captured Bio", cik=cik, source="dynamic_news_discovery")
    return conn


def test_captured_atom_parses_cik_accession_and_index():
    rows = edgar_latest.parse_atom(FIXTURE)
    assert rows and rows[0]["form"] == "8-K"
    assert rows[0]["cik"] == "1035422"
    assert rows[0]["accession"] == "0001477932-26-006099"
    assert rows[0]["accepted"].startswith("2026-10-07T")
    assert rows[0]["filing_index_url"].endswith("-index.htm")


def test_unrelated_sec_equity_mapping_is_not_biotech_universe(tmp_path):
    conn = db.connect(tmp_path / "universe.db")
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("unrelated", "Unrelated Corp", "NOPE", "999", .99, "SEC-v2C-equity", "2026-10-08"))
    assert edgar_latest.biotech_universe(conn) == {}


def test_latest_poll_matches_universe_once_and_prefers_exhibit(tmp_path, monkeypatch):
    cik = edgar_latest.parse_atom(FIXTURE)[0]["cik"]
    conn = _conn(tmp_path, cik)
    base = "https://www.sec.gov/Archives/edgar/data/1/1/"
    def documents(_filing):
        return [{"kind": "primary", "url": base + "primary.htm"},
                {"kind": "ex99", "url": base + "ex99-1.htm"}]
    monkeypatch.setattr(edgar_latest, "filing_documents", documents)
    def fetch_doc(url):
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": "primary.htm"}]}})
        return "Company met its primary endpoint with statistically significant Phase 2 results"
    result = edgar_latest.poll_latest(conn, forms=("8-K",), fetch=lambda _url: FIXTURE,
                                      document_fetch=fetch_doc, now="2026-10-08T07:00:00Z")
    assert result["material"] == 1 and result["seen"] == 1
    row = conn.execute("SELECT new_value,source_url,source_hash FROM change_events").fetchone()
    assert "ex99-1.htm" in row["source_url"] and row["source_hash"]
    assert json.loads(row["new_value"])["outcome"]["polarity"] == "positive"
    assert edgar_latest.poll_latest(conn, forms=("8-K",), fetch=lambda _url: FIXTURE,
                                    document_fetch=fetch_doc)["seen"] == 0


def test_negative_exhibit_and_primary_fallback(tmp_path, monkeypatch):
    cik = edgar_latest.parse_atom(FIXTURE)[0]["cik"]
    conn = _conn(tmp_path, cik)
    monkeypatch.setattr(edgar_latest, "filing_documents", lambda filing: [
        {"kind": "primary", "url": filing["url"]}])
    def fetch_doc(url):
        if url.endswith("index.json"):
            return json.dumps({"directory": {"item": [{"name": "primary.htm"}]}})
        return "Company did not meet its primary endpoint in Phase 3 trial"
    result = edgar_latest.poll_latest(conn, forms=("8-K",), fetch=lambda _url: FIXTURE,
                                      document_fetch=fetch_doc)
    assert result["material"] == 1
    row = conn.execute("SELECT new_value FROM change_events").fetchone()
    assert json.loads(row[0])["outcome"]["polarity"] == "negative"


def test_feed_failure_persists_health(tmp_path):
    conn = db.connect(tmp_path / "failure.db")
    result = edgar_latest.poll_latest(conn, forms=("8-K",), fetch=lambda _url: (_ for _ in ()).throw(OSError("offline")))
    assert result["errors"] == 1
    value = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='sec_latest:8-K'").fetchone()[0])
    assert value["consecutive_failures"] == 1
