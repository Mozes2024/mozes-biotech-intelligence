from datetime import date

from mozes import db
from mozes.live_monitor import (classify_filing, monitor_filing, monitor_study,
                                reconcile_states, record_change)
from mozes.radar import bootstrap_database, validation_status
from mozes.historical import readiness_summary


def _study(status="RECRUITING", pc="2026-12-01", enrollment=100):
    return {"protocolSection": {
        "identificationModule": {"nctId": "NCT12345678"},
        "statusModule": {"overallStatus": status, "primaryCompletionDateStruct": {"date": pc},
                         "completionDateStruct": {"date": "2027-01-01"},
                         "lastUpdatePostDateStruct": {"date": "2026-09-01"}, "whyStopped": None},
        "designModule": {"enrollmentInfo": {"count": enrollment}},
    }}


def test_registry_diff_is_idempotent_and_cannot_verify_catalyst(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    assert monitor_study(conn, _study(), ticker="TEST") == []
    ids = monitor_study(conn, _study(status="COMPLETED", pc="2027-02-01", enrollment=95), ticker="TEST")
    assert len(ids) == 3
    assert monitor_study(conn, _study(status="COMPLETED", pc="2027-02-01", enrollment=95), ticker="TEST") == []
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 3
    assert {r[0] for r in conn.execute("SELECT verification_state FROM change_events")} == {"investigation_only"}
    assert conn.execute("SELECT COUNT(*) FROM event_sources WHERE source_type='clinicaltrials' AND supports_date=1").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM trial_record_versions").fetchone()[0] == 2


def test_sec_financing_dedup_and_escalation(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    filing = {"form": "S-3", "filed": "2026-09-30", "accession": "123", "url": "https://www.sec.gov/test", "accepted": "2026-09-30T16:30:00"}
    first = monitor_filing(conn, "TEST", filing)
    assert first == monitor_filing(conn, "TEST", filing)
    assert classify_filing(filing) == "shelf/registration only"
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 1
    filing["form"] = "424B5"
    filing["accession"] = "124"
    assert classify_filing(filing) == "potential financing/dilution"
    monitor_filing(conn, "TEST", filing)
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 2
    a = dict(ticker="TEST", change_type="sec_material_filing", previous_value=None, new_value="review",
             source_url=filing["url"], source_type="sec", identity="escalate")
    record_change(conn, severity="low", **a)
    record_change(conn, severity="high", **a)
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 4


def test_state_and_recommendation_transitions_once(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    assert reconcile_states(conn, today=date(2026, 9, 30)) == []
    event = db.load_events(conn, "live")[0]
    old = db.event_state(conn, event["id"])
    db.upsert_event_state(conn, event["id"], status="RESOLVED_SUCCESS", verification_state=old["verification_state"],
                          verification_confidence=old["verification_confidence"], note="source-provenanced resolution")
    ids = reconcile_states(conn, today=date(2026, 9, 30))
    kinds = {r[0] for r in conn.execute("SELECT change_type FROM change_events WHERE event_id=?", (event["id"],))}
    assert "catalyst_resolved" in kinds
    assert len(ids) >= 1
    assert reconcile_states(conn, today=date(2026, 9, 30)) == []


def test_recommendation_old_new_and_reason(tmp_path, monkeypatch):
    from mozes import engine_v2
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    current = {"status": "WATCH"}

    def fake_score(_conn, event, _today):
        return {"state": {"status": "SCHEDULED", "verification_state": "VERIFIED"},
                "date": {"window": {"start": "2026-11-01"}}, "sources": [],
                "classification": {"class": "WATCH"}, "recommendation": {"status": current["status"], "why_he": "evidence changed"},
                "security": {"status": "ACTIVE"}}

    monkeypatch.setattr(engine_v2, "score_event", fake_score)
    reconcile_states(conn, today=date(2026, 9, 30))
    current["status"] = "RESEARCH_WORTHY"
    reconcile_states(conn, today=date(2026, 9, 30))
    row = conn.execute("SELECT previous_value,new_value,metadata_json FROM change_events WHERE change_type='recommendation_changed' LIMIT 1").fetchone()
    assert row and row["previous_value"] == '"WATCH"' and row["new_value"] == '"RESEARCH_WORTHY"'
    assert "evidence changed" in row["metadata_json"]


def test_stale_transition_generates_one_change(tmp_path, monkeypatch):
    from mozes import engine_v2
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    current = {"class": "WATCH"}

    def fake_score(_conn, event, _today):
        return {"state": {"status": "SCHEDULED", "verification_state": "VERIFIED"},
                "date": {"window": {"start": "2026-09-01"}}, "sources": [],
                "classification": {"class": current["class"]},
                "recommendation": {"status": "WATCH"}, "security": {"status": "ACTIVE"}}

    monkeypatch.setattr(engine_v2, "score_event", fake_score)
    reconcile_states(conn, today=date(2026, 9, 30))
    current["class"] = "STALE_UNRESOLVED"
    reconcile_states(conn, today=date(2026, 9, 30))
    assert conn.execute("SELECT COUNT(*) FROM change_events WHERE change_type='catalyst_became_stale'").fetchone()[0] == len(db.load_events(conn, "live"))
    reconcile_states(conn, today=date(2026, 9, 30))
    assert conn.execute("SELECT COUNT(*) FROM change_events WHERE change_type='catalyst_became_stale'").fetchone()[0] == len(db.load_events(conn, "live"))


def test_historical_and_validation_untouched(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    before = readiness_summary(conn)
    reconcile_states(conn, today=date(2026, 9, 30))
    assert readiness_summary(conn) == before
    gates = validation_status(conn)
    assert not gates["runup"]["satisfied"] and not gates["hold_through"]["satisfied"]
