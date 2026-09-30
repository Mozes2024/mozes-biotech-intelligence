from mozes import db
from mozes.historical import readiness_for_case
from mozes.radar import bootstrap_database


def test_legacy_seed_is_quarantined_from_historical_validation(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    row = readiness_for_case(conn, "MDGL-2022-12")
    assert not row["research_ready"]
    assert "legacy post-hoc case is quarantined" in row["reasons"]


def test_clean_case_requires_separate_point_in_time_snapshot_and_label(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.upsert_historical_case(conn, "clean", "TEST", "P3_TOPLINE", "2025-01-10", announcement_session="premarket", provenance=["sec"])
    db.store_feature_snapshot(conn, "clean-s", "clean", "2025-01-09T12:00:00", {"prior": "strong"}, provenance=["sec"], blinded=True)
    db.store_outcome_label(conn, "clean-o", "clean", "2025-01-10T16:00:00", {"clinical": "success", "event_return": 0.2}, provenance=["price"], verified=True)
    row = readiness_for_case(conn, "clean")
    assert row["research_ready"] and row["runup_ready"] and row["hold_ready"]
