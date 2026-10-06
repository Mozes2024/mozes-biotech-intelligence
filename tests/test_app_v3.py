from datetime import date

from mozes import db
from mozes.engine_v2 import score_event
from mozes.paper import PaperBook
from mozes.payload_v3 import build
from mozes.radar import bootstrap_database, live_event_records


def test_v3_payload_has_product_sections(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    p = build(conn, date(2026, 9, 30))
    assert p["version"] == "0.3.2"
    assert "summary" in p and "watch_universe" in p and "paper" in p
    assert p["summary"]["live_events"] == len(p["live"])
    assert all("recommendation" in row for row in p["live"])
    assert {"status", "why_he", "missing_he"} <= set(p["live"][0]["recommendation"])
    assert "pipeline" in p["historical_audit"]


def test_v2_analysis_can_be_recorded_in_paper_book(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    event = live_event_records(conn, include_quarantined=True)[0]
    analysis = score_event(conn, event, "2026-09-30")
    sid = PaperBook(conn).record(analysis)
    assert sid.startswith(event["id"] + "__")
    rows = PaperBook(conn).list()
    assert rows and rows[0]["event_id"] == event["id"]
