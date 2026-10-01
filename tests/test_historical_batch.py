import json
import shutil
from collections import Counter
from datetime import timedelta

import pytest

from mozes import db
from mozes.backtest_v2 import report
from mozes.historical import attached_price_rows, import_bundle, market_event_date, readiness_summary
from mozes.historical_batch import (ADJUDICATION, DATA, build_bundle, checked_bundle,
                                    import_checked_batch, inclusion_ids, load_frame)
from mozes.radar import bootstrap_database


def _conn(tmp_path):
    conn = db.connect(tmp_path / "historical.db")
    bootstrap_database(conn)
    return conn


def test_frame_is_complete_and_outcome_free():
    frame = load_frame()
    assert len(frame) == 33
    assert len(inclusion_ids()) == 27
    assert len(checked_bundle().read_bytes()) > 0
    assert all("outcome" not in row and "price" not in row for row in frame.values())


def test_inclusion_is_independent_of_label_and_price(tmp_path):
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    selected = inclusion_ids(copied)
    before = [(case["case_id"], case["ticker"], case["feature_snapshots"])
              for case in build_bundle(copied)["cases"]]
    path = copied / ADJUDICATION
    ledger = json.loads(path.read_text(encoding="utf-8"))
    for row in ledger["included"]:
        row["outcome"] = "fail" if row["outcome"] != "fail" else "success"
        row["outcome_fact"] = "altered after-event statement"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    assert inclusion_ids(copied) == selected
    after = [(case["case_id"], case["ticker"], case["feature_snapshots"])
             for case in build_bundle(copied)["cases"]]
    assert before == after


def test_import_is_idempotent_and_case_bound(tmp_path):
    conn = _conn(tmp_path)
    import_bundle(conn, DATA / "historical_starter.json")
    first = import_checked_batch(conn)
    counts = tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                   ("source_archive", "historical_cases", "feature_snapshots", "outcome_labels",
                    "historical_price_attachments", "historical_case_prices", "price_ingestion_runs"))
    first_price = conn.execute("SELECT close FROM historical_case_prices WHERE case_id='PIT-RYTM-TRANSCEND-20250407' "
                               "AND ticker='RYTM' ORDER BY date LIMIT 1").fetchone()[0]
    second = import_checked_batch(conn)
    assert first["cases"] == second["cases"] == 27
    assert counts == tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
                           ("source_archive", "historical_cases", "feature_snapshots", "outcome_labels",
                            "historical_price_attachments", "historical_case_prices", "price_ingestion_runs"))
    assert conn.execute("SELECT close FROM historical_case_prices WHERE case_id='PIT-RYTM-TRANSCEND-20250407' "
                        "AND ticker='RYTM' ORDER BY date LIMIT 1").fetchone()[0] == first_price
    summary = readiness_summary(conn)
    assert (summary["cases"], summary["runup_ready"], summary["hold_ready"], summary["legacy_quarantined"]) == (51, 27, 0, 21)
    empirical = report(conn)
    assert empirical["coverage"]["runup_ready_cases"] == 27
    assert empirical["hold_through"]["stats"]["n"] == 0
    assert "KOD-2026-09" not in empirical["coverage"]["runup_case_ids"]
    for case in db.historical_case_rows(conn):
        if not case["case_id"].startswith("PIT-") or case["case_id"] in (
            "PIT-KOD-DAYBREAK-20260928", "PIT-QTTB-SIGNALAA-20260713", "PIT-PHAR-JOENJA-20260911"):
            continue
        day = market_event_date(case["event_at"])
        stock, xbi = attached_price_rows(conn, case)
        for rows in (stock, xbi):
            assert rows[0]["date"] <= (day - timedelta(days=120)).isoformat()
            assert rows[-1]["date"] >= (day + timedelta(days=30)).isoformat()


def test_source_and_price_provenance_cannot_be_silently_changed(tmp_path):
    conn = _conn(tmp_path)
    import_checked_batch(conn)
    row = conn.execute("SELECT * FROM source_archive WHERE source_id='NAMS-BROOKLYN-20240729-pre'").fetchone()
    with pytest.raises(ValueError, match="different provenance"):
        db.archive_source(conn, row["source_id"], "https://different.example", row["source_type"],
                          row["published_at"], row["retrieved_at"], row["content"], json.loads(row["metadata_json"]))
    with pytest.raises(ValueError, match="different frozen price attachment"):
        db.attach_historical_prices(conn, "PIT-NAMS-BROOKLYN-20240729", "NAMS", "2024-01-01", "2024-12-31", "csv")


def test_market_date_does_not_roll_afterhours_events_into_next_day():
    assert market_event_date("2024-11-23T00:11:00Z").isoformat() == "2024-11-22"
    assert market_event_date("2025-12-13T01:00:00Z").isoformat() == "2025-12-12"
    case = next(c for c in build_bundle()["cases"] if c["case_id"] == "PIT-BBIO-ACORAMIDIS-20241129")
    assert case["feature_snapshots"][-1]["as_of"] == "2024-11-21T23:59:59Z"


def test_batch_has_failures_and_crls_not_only_winners():
    labels = [case["outcome_label"]["label"] for case in build_bundle()["cases"]]
    counts = Counter(label.get("clinical") or label.get("regulatory") for label in labels)
    assert counts["fail"] >= 1 and counts["crl"] >= 1 and counts["approve"] >= 1
    assert all(case["feature_snapshots"] and case["outcome_label"] for case in build_bundle()["cases"])


def test_expanded_cases_keep_validation_gates_locked(tmp_path):
    conn = _conn(tmp_path)
    before = {name: dict(db.validation_row(conn, name)) for name in ("runup", "hold_through")}
    import_checked_batch(conn)
    after = {name: dict(db.validation_row(conn, name)) for name in ("runup", "hold_through")}
    assert after == before
    assert all(not gate["enabled"] for gate in after.values())


def test_pre_event_snapshots_use_only_sources_available_at_their_cutoff():
    bundle = build_bundle()
    sources = {row["source_id"]: row for row in bundle["sources"]}
    for case in bundle["cases"]:
        outcome_id = case["outcome_label"]["source_ids"][0]
        for snapshot in case["feature_snapshots"]:
            assert outcome_id not in snapshot["source_ids"]
            assert all(sources[source_id]["published_at"] <= snapshot["as_of"]
                       for source_id in snapshot["source_ids"])


def test_checked_price_capture_rejects_modified_csv(tmp_path):
    copied = tmp_path / "data"
    shutil.copytree(DATA, copied)
    csv = copied / "candidate_prices_2024_2026" / "NAMS.csv"
    csv.write_bytes(csv.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="price CSV changed"):
        import_checked_batch(_conn(tmp_path), copied)
