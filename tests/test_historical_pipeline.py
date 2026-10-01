from mozes import db
import json
from datetime import date, timedelta

import pytest

from mozes.historical import attach_price_coverage, import_bundle, readiness_for_case
from mozes.radar import bootstrap_database


def test_legacy_seed_is_quarantined_from_historical_validation(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    row = readiness_for_case(conn, "MDGL-2022-12")
    assert not row["research_ready"]
    assert "legacy post-hoc case is quarantined" in row["reasons"]


def test_clean_case_requires_separate_point_in_time_snapshot_and_label(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.archive_source(conn, "sec", "https://sec.test/pre", "sec_8k", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", "pre-event evidence")
    db.archive_source(conn, "outcome", "https://sec.test/post", "sec_8k", "2025-01-10T12:00:00Z", "2025-01-11T00:00:00Z", "outcome evidence")
    db.upsert_historical_case(conn, "clean", "TEST", "P3_TOPLINE", "2025-01-10", announcement_session="premarket", provenance=["sec"])
    db.store_feature_snapshot(conn, "clean-s", "clean", "2025-01-09T12:00:00", {"prior": "strong"}, provenance=["sec"], blinded=True)
    db.store_outcome_label(conn, "clean-o", "clean", "2025-01-10T16:00:00", {"clinical": "success", "event_return": 0.2}, provenance=["outcome"], verified=True)
    for ticker in ("TEST", "XBI"):
        db.store_prices(conn, ticker, [{"date": (date(2024, 8, 1) + timedelta(days=day)).isoformat(), "close": 10 + day} for day in range(195)], "csv")
    attach_price_coverage(conn, "clean", "csv")
    row = readiness_for_case(conn, "clean")
    assert row["research_ready"] and row["runup_ready"] and row["hold_ready"]


def test_import_rejects_hindsight_source_and_is_idempotent(tmp_path):
    bundle = {"sources": [
        {"source_id": "pre", "canonical_url": "https://example.test/pre", "source_type": "sec_8k", "published_at": "2025-01-01T10:00:00Z", "content": "before"},
        {"source_id": "post", "canonical_url": "https://example.test/post", "source_type": "company_ir", "published_at": "2025-01-11T10:00:00Z", "content": "after"},
    ], "cases": [{"case_id": "PIT-1", "ticker": "TEST", "catalyst_type": "P3_TOPLINE", "event_at": "2025-01-10T13:00:00Z", "announcement_session": "premarket", "source_ids": ["post"], "feature_snapshots": [{"snapshot_id": "PIT-1-T1", "horizon": "T-1", "as_of": "2025-01-09T16:00:00Z", "source_ids": ["pre"], "features": {"trial": "known"}}]}]}
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    conn = db.connect(tmp_path / "x.db")
    assert import_bundle(conn, path)["cases"] == 1
    assert import_bundle(conn, path)["cases"] == 1
    bundle["cases"][0]["feature_snapshots"][0]["source_ids"] = ["post"]
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="published after"):
        import_bundle(conn, path)


def test_archive_is_immutable_and_outcomes_are_not_features(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    db.archive_source(conn, "s", "https://example.test", "sec_8k", "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", "one")
    with pytest.raises(ValueError, match="different content"):
        db.archive_source(conn, "s", "https://example.test", "sec_8k", "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z", "two")
    db.upsert_historical_case(conn, "sep", "TEST", "P3_TOPLINE", "2025-01-10T12:00:00Z", announcement_session="premarket", provenance=["s"])
    db.store_feature_snapshot(conn, "sep-s", "sep", "2025-01-09T12:00:00Z", {"endpoint": "ORR"}, provenance=["s"], blinded=True)
    db.store_outcome_label(conn, "sep-o", "sep", "2025-01-10T13:00:00Z", {"clinical": "fail"}, provenance=["s"], verified=True)
    assert "clinical" not in json.loads(db.feature_snapshot_rows(conn, "sep")[0]["payload"])


def test_import_requires_an_announcement_timestamp(tmp_path):
    bundle = {"sources": [{"source_id": "s", "canonical_url": "https://example.test", "source_type": "sec_8k", "published_at": "2025-01-01T00:00:00Z", "content": "x"}], "cases": [{"case_id": "bad", "ticker": "TEST", "catalyst_type": "P3_TOPLINE", "event_at": "2025-01-10", "source_ids": ["s"]}]}
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="announcement timestamp"):
        import_bundle(db.connect(tmp_path / "x.db"), path)
