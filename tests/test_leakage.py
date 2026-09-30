"""RELEASE-BLOCKING: point-in-time integrity tests."""
import pytest

from mozes.ai.provider import build_evidence_prompt
from mozes.data_loader import load_historical, load_live, load_outcomes
from mozes.pit import OUTCOME_KEYS, LeakageError, all_keys, assert_point_in_time, snapshot_at, thaw
from mozes.scenarios import reference_class
from mozes.scoring import clinical_evidence

AS_OF = "2021-01-15"
E = {
    "id": "FIX", "ticker": "FIX", "type": "P3_TOPLINE", "date": AS_OF,
    "documents": [
        {"published": "2021-01-10", "text": "pre"},
        {"published": "2021-01-15", "text": "RESULT DAY"},
        {"published": "2021-01-20", "text": "post"},
        {"published": "2021-01", "text": "month-precision"},
    ],
    "prices": [{"date": d, "close": 1.0} for d in ("2021-01-13", "2021-01-14", "2021-01-15", "2021-01-18")],
    "trial_versions": [{"date": "2020-01-01", "primary_endpoint": "A"},
                       {"date": "2021-02-01", "primary_endpoint": "B (revised after readout)"}],
    "analyst": [{"date": "2020-12-01", "target": 10}, {"date": "2021-01-16", "target": 50}],
    "features": {"prior": "strong", "design": "rct_placebo"}, "features_as_of": "2020-12",
    "flags": [{"sev": "high", "code": "ok", "known_from": "2021-01-01", "text": "known"},
              {"sev": "high", "code": "late", "known_from": "2021-01-16", "text": "late"},
              {"sev": "high", "code": "undated", "text": "no date"}],
}


def test_documents_on_or_after_as_of_excluded():
    assert [d["text"] for d in snapshot_at(E, AS_OF)["documents"]] == ["pre"]


def test_month_precision_document_is_conservative():
    assert "month-precision" not in [d["text"] for d in snapshot_at(E, AS_OF)["documents"]]


def test_prices_on_or_after_as_of_excluded():
    s = snapshot_at(E, AS_OF)
    assert [p["date"] for p in s["prices"]] == ["2021-01-13", "2021-01-14"]


def test_trial_registry_version_is_point_in_time():
    assert snapshot_at(E, AS_OF)["trial_record"]["primary_endpoint"] == "A"


def test_analyst_targets_after_as_of_excluded():
    assert [a["target"] for a in snapshot_at(E, AS_OF)["analyst"]] == [10]


def test_flags_late_or_undated_excluded():
    assert [f["code"] for f in snapshot_at(E, AS_OF)["flags"]] == ["ok"]


def test_features_known_later_are_ignored():
    late = dict(E, features_as_of="2021-02")
    s_late, s_ok = snapshot_at(late, AS_OF), snapshot_at(E, AS_OF)
    assert dict(s_late["features"]) == {}
    assert clinical_evidence(s_late)["score"] < clinical_evidence(s_ok)["score"]


def test_snapshot_is_immutable():
    s = snapshot_at(E, AS_OF)
    with pytest.raises(TypeError):
        s["features"]["prior"] = "weak"
    with pytest.raises(TypeError):
        s["as_of"] = "2030-01-01"


def test_assert_point_in_time_detects_injected_leak():
    leaked = thaw(snapshot_at(E, AS_OF))
    leaked["documents"].append({"published": "2021-02-01", "text": "future"})
    with pytest.raises(LeakageError):
        assert_point_in_time(leaked)
    leaked2 = thaw(snapshot_at(E, AS_OF))
    leaked2["prices"].append({"date": "2021-01-15", "close": 9})
    with pytest.raises(LeakageError):
        assert_point_in_time(leaked2)


def test_snapshot_rejects_events_carrying_outcomes():
    with pytest.raises(LeakageError):
        snapshot_at(dict(E, outcome="success"), AS_OF)


def test_dataset_events_never_contain_outcome_fields():
    for e in load_historical() + load_live():
        assert not (all_keys(e) & OUTCOME_KEYS), e["id"]


def test_prompt_excludes_outcomes_and_future_documents():
    hist, outcomes = load_historical(), load_outcomes()
    kod = next(h for h in hist if h["id"] == "KOD-2026-09")
    prompt = build_evidence_prompt(snapshot_at(kod, kod["date"]))
    assert "met primary" not in prompt.lower()
    assert outcomes[kod["id"]]["move_basis"] not in prompt
    assert '"clinical"' not in prompt and '"move"' not in prompt


def test_reference_class_uses_only_previously_resolved_events():
    hist, outcomes = load_historical(), load_outcomes()
    dates = {h["id"]: h["date"] for h in hist}
    for h in hist:
        rc = reference_class(h["type"], h["date"], hist, outcomes)
        assert h["id"] not in rc["ids"]
        assert all(dates[i] < h["date"] for i in rc["ids"])


def test_reference_class_empty_before_dataset_start():
    assert reference_class("P3_TOPLINE", "2020-01-01", load_historical(), load_outcomes())["ids"] == []


def test_live_snapshots_pass_point_in_time_validation():
    for e in load_live():
        assert_point_in_time(snapshot_at(e, "2026-10-01"))
