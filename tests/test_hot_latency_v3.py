"""Persisted latency and source freshness contracts for the hot dashboard."""
from mozes import db
from mozes.latency_metrics import summary
from mozes.live_monitor import observe


def test_latency_percentiles_and_missing_timestamps(tmp_path):
    conn = db.connect(tmp_path / "latency.db")
    for i, seconds in enumerate((10, 20, 30, 40, 50)):
        conn.execute(
            "INSERT INTO latency_events(latency_id,change_id,source_type,source_published_at,"
            "source_first_seen_at,alert_queued_at,alert_sent_at) VALUES(?,?,?,?,?,?,?)",
            (str(i), str(i), "sec", "2026-10-08T00:00:00Z",
             f"2026-10-08T00:00:{seconds:02d}Z", None, None),
        )
    conn.execute(
        "INSERT INTO latency_events(latency_id,change_id,source_type,source_first_seen_at) "
        "VALUES('missing','missing','wire','2026-10-08T00:01:00Z')")
    result = summary(conn)
    detection = result["metrics"]["publication_to_first_seen"]
    assert detection == {"n": 5, "p50": 30.0, "p95": 50.0, "p99": 50.0, "mean": 30.0}
    assert result["metrics"]["publication_to_sent"]["p50"] is None
    assert result["by_source_type"]["wire"]["metrics"]["publication_to_first_seen"]["n"] == 0


def test_source_failure_is_persisted_and_visible(tmp_path):
    conn = db.connect(tmp_path / "freshness.db")
    observe(conn, "wire_feed:test", {"status": "active", "checked_at": "2026-10-08T00:00:00Z"})
    observe(conn, "wire_feed:test", {"status": "fetch_error", "checked_at": "2026-10-08T00:00:15Z"})
    observe(conn, "wire_feed:test", {"status": "fetch_error", "checked_at": "2026-10-08T00:00:30Z"})
    import json
    value = json.loads(conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key='wire_feed:test'").fetchone()[0])
    assert value["last_success_at"] == "2026-10-08T00:00:00Z"
    assert value["last_error_at"] == "2026-10-08T00:00:30Z"
    assert value["consecutive_failures"] == 2
