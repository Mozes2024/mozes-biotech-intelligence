"""Public-source classification, immutable history and presentation contracts."""
import contextlib
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from mozes import db
from mozes.alert_correction import CHANGE_ID, EDGE_ID, SOURCE_HASH, prepare, apply, rollback
from mozes.alert_dispatch import alert_priority, build_stage1_payload, material_story_state, recent_alerts
from mozes.alert_explanation import _quote, _optional_ai, explain, normalize_source
from mozes.alert_feed import build_feed
from mozes.clinical_events import classify_clinical
from mozes.materiality import classify_outcome

FIXTURES = Path(__file__).parent / "fixtures"
RAW = (FIXTURES / "atos_20261008_ex99_1.html").read_bytes().decode()
URL = "https://www.sec.gov/Archives/edgar/data/1488039/000119312526418560/atos-ex99_1.htm"


@pytest.mark.parametrize("document", ["atos_20261008_8k.html", "atos_20261008_ex99_1.html"])
def test_exact_atos_primary_documents_are_conditional_corporate_not_approval(document):
    text = normalize_source((FIXTURES / document).read_text(encoding="utf-8"))
    outcome = classify_clinical(text)
    assert outcome["event_family"] == "corporate_action"
    assert outcome["event_outcome"] == "conditional_cvr"
    assert outcome["polarity"] == "unknown" and not outcome["urgent"]
    assert alert_priority("sec_material_filing", "high", outcome, ticker="ATOS", verified=True) == "P2"
    assert alert_priority("sec_material_filing", "critical", outcome, ticker="ATOS", watched=True, verified=True) == "P2"
    if "ex99" in document:
        assert hashlib.sha256(RAW.encode()).hexdigest() == SOURCE_HASH
        explanation = explain({"priority": "P2", "new_value": {"headline": text},
            "source_url": URL, "verification_state": "primary_source"}, text, basis="source_text", source_hash=SOURCE_HASH)
        assert all(term in explanation["summary_he"] for term in ("October 19, 2026", "25%", "$50 million", "אין אישור FDA", "לא הוענק PRV", "CVR אחד לכל מניה זכאית"))
        assert any(e["quote"].startswith("No Atossa product candidate") for e in explanation["evidence"])
        assert all(e["quote"] in text and e["url"] == URL for e in explanation["evidence"])
        assert explanation["source_hash"] == SOURCE_HASH
        assert {e["id"] for e in explanation["evidence"]} >= {"source-absence", "source-cvr-economics", "source-cvr-record-date"}


@pytest.mark.parametrize("prefix", ["No", "Not one", "Never has any", "Without any evidence that a"])
def test_long_subject_negation_is_not_approval_or_rejection(prefix):
    text = prefix + " product candidate developed in this extensive clinical research program has been approved by the FDA for any indication."
    outcome = classify_outcome(text)
    assert outcome["polarity"] == "unknown" and not outcome["urgent"]
    state = material_story_state({"new_value": {"summary": text}})
    assert not state["approval"] and not state["crl"]
    quote = _quote(text, re.search("approved", text), words=100)
    assert quote == text


@pytest.mark.parametrize("text,expected", [
    ("If the FDA approves this investigational program, the company may receive a PRV.", "unconfirmed"),
    ("The company received FDA Fast Track designation.", "designation"),
    ("The company submitted a marketing application to FDA.", "submission"),
    ("The application was accepted for priority review.", "review_acceptance"),
    ("FDA accepted the application for priority review following a Phase 3 trial.", "unconfirmed"),
    ("The application was accepted for priority review for the Phase 3 program.", "review_acceptance"),
    ("FDA approval is expected next year.", "unconfirmed"),
    ("FDA approval of the investigational product could create future value.", "unconfirmed"),
    ("FDA approved the drug in 2020.", "unconfirmed"),
    ("No product candidate has been approved by the FDA.", "explicit_absence"),
])
def test_regulatory_milestones_do_not_claim_new_approval(text, expected):
    outcome = classify_outcome(text)
    assert outcome["event_outcome"] == expected and not outcome["urgent"]
    assert not material_story_state({"new_value": {"summary": text}})["approval"]
    if expected == "explicit_absence":
        assert classify_clinical(text)["actionable"] is False


@pytest.mark.parametrize("text", [
    "FDA approved the new therapy today.",
    "The company received a complete response letter from FDA today.",
    "The pivotal Phase 3 trial did not meet its primary endpoint.",
    "FDA placed the program on a clinical hold.",
    "The pivotal Phase 3 trial met its primary endpoint.",
    "No other candidate has been approved by the FDA. FDA approved Drug X today.",
    "The pivotal Phase 3 trial met its primary endpoint. The company also has contingent value rights.",
])
def test_verified_non_watchlist_urgent_developments_remain_p1(text):
    outcome = classify_outcome(text)
    assert outcome["urgent"] and outcome["material"]
    assert alert_priority("sec_material_filing", "high", outcome, ticker="ZZZZ", verified=True, watched=False) == "P1"
    assert alert_priority("sec_material_filing", "high", outcome, ticker="ZZZZ", verified=False, watched=True) == "P2"


def test_verified_identity_is_not_watchlist_membership(tmp_path, monkeypatch):
    from mozes.live_monitor import record_change
    monkeypatch.setattr("mozes.alert_dispatch.verified_issuer_identity", lambda *a: {"cik": "123"})
    monkeypatch.setattr("mozes.alert_dispatch.is_watched", lambda *a: False)
    with contextlib.closing(db.connect(tmp_path / "issuer.db")) as conn:
        cid = record_change(conn, ticker="ZZZZ", change_type="sec_material_filing", severity="high",
            previous_value=None, new_value={"headline": "FDA approved the new therapy today."},
            source_url="https://www.sec.gov/news/test", source_type="sec", verification_state="primary_source")
        payload = build_stage1_payload(conn, cid)
        assert payload["priority"] == "P1" and payload["watched"] is False
        assert payload["verified_issuer_cik"] == "123"


@pytest.mark.parametrize("text,family,priority", [
    ("Form 8-K reports annual meeting voting results.", "routine_administration", "P3"),
    ("Company entered a definitive agreement to be acquired by Buyer.", "merger_acquisition", "P2"),
    ("Company announced a $350 million public offering.", "financing", "P2"),
])
def test_administration_and_corporate_economics_are_not_p1(text, family, priority):
    outcome = classify_outcome(text)
    assert outcome["event_family"] == family
    assert alert_priority("sec_material_filing", "high", outcome, ticker="ZZZZ", watched=True, verified=True) == priority


def test_quote_omits_unsafe_long_sentence_and_preserves_wrapped_negation():
    text = "No\n" + "extensive investigational " * 70 + "product has been approved by the FDA."
    assert _quote(text, re.search("approved", text), words=100) == ""
    explanation = explain({"new_value": {"headline": text}}, text, basis="source_text")
    assert not explanation["evidence"] and any("ציטוט בטוח" in x for x in explanation["missing_he"])
    text = "If\n the extensive program succeeds, the FDA may approve a product."
    assert _quote(text, re.search("FDA", text), words=100) == text


def test_optional_ai_cannot_reverse_absence(monkeypatch):
    monkeypatch.setenv("MOZES_ALERT_AI", "1")
    result = explain({"new_value": {}}, "No product has been approved by the FDA.", basis="source_text")
    class Contradictory:
        name = "test"
        def complete(self, prompt):
            raise AssertionError("unvalidated paraphrase must never run")
    guarded = _optional_ai(result, Contradictory())
    assert guarded["summary_he"] == result["summary_he"] and guarded["ai_status"] == "evidence_guard"


def test_legacy_outcome_and_new_policy_compare_identically_for_deduplication():
    text = "FDA approved the new therapy today."
    old = {"new_value": {"headline": text}, "change_type": "sec_material_filing",
           "outcome": {"polarity": "positive", "material": True, "method": "deterministic_regex_v2"}}
    new = {**old, "outcome": classify_outcome(text)}
    assert material_story_state(old) == material_story_state(new)
    assert material_story_state(old)["approval"]


def correction_fixture(path):
    from mozes.live_monitor import record_change
    from mozes.alert_dispatch import dispatch_pending
    from mozes.alert_explanation import enrich_pending
    conn = db.connect(path)
    db.upsert_watch(conn, "ATOS", cik="1488039", company="Atossa Therapeutics", source="dynamic_news_discovery")
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("atossa", "Atossa Therapeutics", "ATOS", "1488039", .99, "SEC-v2C-equity", "2026-10-10"))
    cid = record_change(conn, ticker="ATOS", change_type="sec_material_filing", severity="high",
        previous_value=None, new_value={"accession": "0001193125-26-418560", "headline": "Contingent value rights agreement",
            "summary": normalize_source(RAW), "outcome": {"polarity": "positive", "material": True}},
        source_url=URL, source_type="sec", verification_state="primary_source", source_hash=SOURCE_HASH,
        identity=["sec_latest", "0001193125-26-418560"])
    assert cid == CHANGE_ID
    db.archive_source(conn, EDGE_ID + "-SRC-" + SOURCE_HASH, URL, "sec", "2026-10-09", "2026-10-10", RAW, {"edge_event_id": EDGE_ID})
    conn.execute("INSERT INTO edge_event_links VALUES(?,?,?,?,?)", (EDGE_ID, cid, "38043306176", "2026-10-10", SOURCE_HASH))
    conn.commit()
    dispatch_pending(conn)
    enrich_pending(conn, fetch=lambda *a: RAW)
    # Reproduce the OLD payload and old immutable analysis. Neither is rewritten.
    row = conn.execute("SELECT alert_id,payload_json FROM alert_outbox").fetchone()
    payload = json.loads(row[1]); payload.update(priority="P1", outcome={"polarity": "positive", "material": True}, edge_event_ids=[EDGE_ID])
    conn.execute("UPDATE alert_outbox SET payload_json=? WHERE alert_id=?", (json.dumps(payload), row[0]))
    conn.commit()
    return conn


def test_display_correction_is_idempotent_preserves_receipts_and_revises_feed(tmp_path, monkeypatch):
    for key in ("MOZES_NTFY_URL", "MOZES_SMTP_USER", "MOZES_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)
    with contextlib.closing(correction_fixture(tmp_path / "correction.db")) as conn:
        original = build_feed(conn)
        old_analysis = list(conn.execute("SELECT * FROM alert_analyses"))
        plan = prepare(conn)
        assert apply(conn, plan)["status"] == "CORRECTED"
        corrected = build_feed(conn)
        assert original["revision"] != corrected["revision"]
        alert = corrected["alerts"][0]
        assert alert["change_id"] == CHANGE_ID and alert["edge_event_ids"] == [EDGE_ID]
        assert alert["priority"] == "P2" and alert["polarity"] == "unknown" and not alert["popup_eligible"]
        assert alert["delivery"]["log"]["status"] == "sent"
        assert apply(conn, plan)["status"] == "ALREADY_CORRECTED"
        assert build_feed(conn)["revision"] == corrected["revision"]
        assert all(tuple(row) in [tuple(x) for x in conn.execute("SELECT * FROM alert_analyses")] for row in old_analysis)
        from mozes.edge_enrichment import ack_payload
        before_ack = ack_payload(conn, EDGE_ID)
        assert before_ack["change_id"] == CHANGE_ID and before_ack["delivery"][0]["status"] == "sent"
        from mozes.edge_enrichment import process
        before_changes = conn.total_changes
        event = {"edge_event_id": EDGE_ID, "source": "sec", "ticker": "ATOS", "cik": "1488039",
                 "accession": "0001193125-26-418560", "source_url": URL, "analysis_status": "complete", "material": 1}
        assert process(conn, event, github_run_id="42", fetch=lambda _: pytest.fail("completed event must not refetch")) == CHANGE_ID
        assert conn.total_changes == before_changes
        rollback(conn, plan["analysis_id"]); rollback(conn, plan["analysis_id"])
        assert prepare(conn)["history"] == plan["history"]
        assert build_feed(conn)["revision"] == original["revision"]


def test_correction_fails_closed_on_source_or_receipt_changes(tmp_path):
    with contextlib.closing(correction_fixture(tmp_path / "guards.db")) as conn:
        plan = prepare(conn)
        conn.execute("UPDATE alert_outbox SET attempts=2"); conn.commit()
        with pytest.raises(ValueError, match="ambiguous"):
            apply(conn, plan)
        assert conn.execute("SELECT COUNT(*) FROM alert_analyses").fetchone()[0] == 1
        conn.execute("UPDATE alert_outbox SET attempts=1"); conn.commit()
        conn.execute("UPDATE edge_event_links SET source_hash='wrong'"); conn.commit()
        with pytest.raises(ValueError, match="mismatch"):
            prepare(conn)


def test_correction_stale_plan_and_newer_analysis_rollback_are_rejected(tmp_path):
    with contextlib.closing(correction_fixture(tmp_path / "stale.db")) as conn:
        plan = prepare(conn)
        conn.execute("UPDATE alert_analysis_jobs SET attempts=attempts+1"); conn.commit()
        with pytest.raises(ValueError, match="stale"):
            apply(conn, plan)
        plan = prepare(conn); apply(conn, plan)
        conn.execute("INSERT INTO alert_analyses VALUES('ANA-newer',?,NULL,'future-version','2026-10-10','{}')", (CHANGE_ID,))
        conn.execute("UPDATE alert_analysis_jobs SET analysis_id='ANA-newer'"); conn.commit()
        with pytest.raises(ValueError, match="newer analysis"):
            rollback(conn, plan["analysis_id"])
