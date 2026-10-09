import io
import sqlite3
import urllib.error
from datetime import date, datetime, timezone, timedelta
from contextlib import closing

import pytest

from mozes.sec_coverage_audit import audit_issuer, filing_rows
from mozes.wire_transport import fetch_wire, fetch_with_cooldown


class Response(io.BytesIO):
    def geturl(self):
        return "https://www.businesswire.com/news/home/example"


def test_wire_timeout_retry_is_bounded_and_closes_response():
    calls, sleeps = [], []
    response = Response(b"primary source")
    def opener(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise TimeoutError()
        return response
    assert fetch_wire(response.geturl(), validate_redirect=lambda x: None, user_agent="test",
                      opener=opener, sleeper=sleeps.append) == "primary source"
    assert calls == [20, 20] and sleeps == [2] and response.closed


@pytest.mark.parametrize("code,header,attempts", [(403, None, 1), (429, "60", 1),
    (503, "30", 1), (503, "2", 2), (503, "invalid", 1)])
def test_wire_access_restrictions_and_retry_after(code, header, attempts):
    calls = []
    def opener(request, timeout):
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, code, "error", {"Retry-After": header}, None)
    with pytest.raises(urllib.error.HTTPError):
        fetch_wire("https://www.businesswire.com/news/home/example", validate_redirect=lambda x: None,
                   user_agent="test", opener=opener, sleeper=lambda x: None)
    assert len(calls) == attempts


def test_wire_repeated_timeout_stops_without_synthesizing_source():
    calls = []
    def opener(request, timeout):
        calls.append(1)
        raise TimeoutError()
    with pytest.raises(TimeoutError):
        fetch_wire("https://www.businesswire.com/news/home/example", validate_redirect=lambda x: None,
                   user_agent="test", opener=opener, sleeper=lambda x: None)
    assert len(calls) == 2


def block(acc="0001193125-26-417939", filed="2026-10-08", form="8-K"):
    return {"accessionNumber": [acc], "filingDate": [filed], "form": [form]}


def test_sec_inventory_dates_forms_and_capture_are_separate():
    result = audit_issuer("34956", {"ticker": "TENX"},
        fetch=lambda url: {"filings": {"recent": block(), "files": []}},
        start=date(2026, 10, 8), end=date(2026, 10, 9), known={"000119312526417939"})
    assert result["filings"][0]["stored_capture"] is True
    assert filing_rows(block(form="10-Q"), date(2026, 10, 8), date(2026, 10, 9)) == []


def test_sec_archive_inventory_is_bounded_and_reports_missing():
    calls = []
    archive = {"name": "CIK0000034956-submissions-001.json", "filingFrom": "2026-10-01", "filingTo": "2026-10-09"}
    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            return {"filings": {"recent": block(filed="2026-10-10"), "files": [archive]}}
        return block()
    result = audit_issuer("34956", {"ticker": "TENX"}, fetch=fetch,
        start=date(2026, 10, 8), end=date(2026, 10, 9), known=set())
    assert len(calls) == 2 and result["filings"][0]["stored_capture"] is False
    with pytest.raises(ValueError):
        audit_issuer("34956", {"ticker": "TENX"}, fetch=lambda url: {"filings": {
            "recent": block(filed="2026-10-10"), "files": [archive] * 3}},
            start=date(2026, 10, 8), end=date(2026, 10, 9), known=set())


def test_sec_malformed_parallel_arrays_fail_closed():
    with pytest.raises(ValueError):
        filing_rows({**block(), "form": []}, date(2026, 10, 8), date(2026, 10, 9))


@pytest.mark.parametrize("code,header,wait", [(403, None, 3600), (429, "7200", 7200)])
def test_wire_backoff_survives_reopen_and_never_becomes_ack(tmp_path, code, header, wait):
    path = tmp_path / "monitor.db"
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    url = "https://www.businesswire.com/news/home/example"
    calls = []
    def denied(target):
        calls.append(target)
        raise urllib.error.HTTPError(target, code, "blocked", {"Retry-After": header}, None)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE monitor_observations (observation_key TEXT PRIMARY KEY,value_json TEXT,content_hash TEXT,source_url TEXT,source_type TEXT,observed_at TEXT)")
        with pytest.raises(urllib.error.HTTPError):
            fetch_with_cooldown(conn, url, fetch=denied, now=now)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(RuntimeError, match="backoff active"):
            fetch_with_cooldown(conn, url, fetch=denied, now=now + timedelta(seconds=wait - 1))
        assert len(calls) == 1
        assert fetch_with_cooldown(conn, url, fetch=lambda x: "verified source", now=now + timedelta(seconds=wait)) == "verified source"
        assert conn.execute("SELECT count(*) FROM monitor_observations").fetchone()[0] == 1


def test_edge_backoff_persists_without_change_alert_or_ack(tmp_path, monkeypatch):
    from test_edge_enrichment_v31 import setup, event
    from mozes.edge_enrichment import process, ack_payload
    from mozes import wire_transport
    calls = []
    def blocked(url, **kwargs):
        calls.append(url)
        raise urllib.error.HTTPError(url, 403, "blocked", {}, None)
    monkeypatch.setattr(wire_transport, "fetch_wire", blocked)
    with closing(setup(tmp_path)) as conn:
        with pytest.raises(urllib.error.HTTPError):
            process(conn, event(), github_run_id="42")
        with pytest.raises(RuntimeError, match="backoff active"):
            process(conn, event(), github_run_id="43")
        assert len(calls) == 1
        assert conn.execute("SELECT count(*) FROM change_events").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM alert_outbox").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM edge_event_links").fetchone()[0] == 0
        with pytest.raises(RuntimeError, match="no durable"):
            ack_payload(conn, event()["edge_event_id"])
