import json
from contextlib import closing

import pytest

from mozes import db
from mozes.intelligence_store import ensure_schema
from mozes.promotion import candidate_matches_statement, promote_candidate


def test_exact_nct_is_high_confidence_match():
    c = {"nct_id":"NCT12345678","title":"Trial of Drug X","raw_json":"{}"}
    ok, conf, why = candidate_matches_statement(c, "Topline results from NCT12345678 are expected in December 2026.")
    assert ok and conf == 1.0


def test_unrelated_statement_does_not_promote():
    c = {"nct_id":"NCT12345678","title":"Trial of Drug X","raw_json":"{}"}
    ok, conf, why = candidate_matches_statement(c, "We expect revenue guidance in December 2026.")
    assert not ok


def registered_candidate(**changes):
    raw = {"identificationModule": {"acronym": "FOCUS-3"},
           "armsInterventionsModule": {"interventions": [
               {"name": "ABC-123", "otherNames": ["ABC123", "Specificmab"]},
               {"name": "Placebo", "otherNames": ["placebo oral tablet"]},
           ]}}
    return {"candidate_id": "C", "nct_id": "NCT12345678", "phase": "PHASE3",
            "title": "Safety efficacy evaluation", "raw_json": json.dumps({"protocolSection": raw}),
            **changes}


def primary_statement(text):
    url = "https://www.sec.gov/Archives/edgar/data/123/a/ex99.htm"
    return {"statement": text, "source_id": url, "source_url": url,
            "published_at": "2026-10-05", "retrieved_at": "2026-10-05T12:00:00Z",
            "window": {"start": "2026-12-01",
            "end": "2026-12-31", "precision": "month", "original": "December 2026"}}


@pytest.fixture
def conn(tmp_path):
    with closing(db.connect(tmp_path / "promotion.db")) as c:
        ensure_schema(c)
        yield c


def test_M2_generic_words_do_not_verify():
    # Ordinary regression converted from the independent audit's M2 xfail.
    got = candidate_matches_statement({"title": "Safety efficacy evaluation", "raw_json": "{}"},
                                     "Novel Bio discusses safety and efficacy in unrelated programs")
    assert got[0] is False and got[1] == 0


def test_M2_generic_statement_not_auto_promoted(conn):
    # The audit input previously created AUTO-ZZZZ-NCT12345678 at confidence 97.
    candidate = registered_candidate(raw_json="{}")
    statement = primary_statement("Novel Bio expects safety and efficacy data for unrelated programs in December 2026.")
    assert promote_candidate(conn, candidate, statement, "ZZZZ") is None
    assert promote_candidate(conn, candidate, statement, "ZZZZ") is None
    for table in ("events", "event_state", "change_events", "alert_outbox", "event_sources"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    row = conn.execute("SELECT outcome,reason FROM v2e2_promotion_decisions").fetchone()
    assert tuple(row) == ("REJECTED", "no_candidate_match")
    assert conn.execute("SELECT COUNT(*) FROM v2e2_promotion_decisions").fetchone()[0] == 1


@pytest.mark.parametrize("statement", [
    "nct12345678 reports data.", "Results from (NCT12345678).", "NCT12345678: December data.",
])
def test_exact_nct_case_and_punctuation(statement):
    assert candidate_matches_statement(registered_candidate(), statement)[:2] == (True, 1.0)


@pytest.mark.parametrize("statement", [
    "XNCT12345678", "NCT123456789", "NCT12345678A", "NCT12345678-1",
    "ABC-1234 reports data", "XABC-123 reports data", "ABC-123-A reports data",
    "Unspecificmab data", "FOCUS-30 data", "Placebo oral tablet data",
    "Safety efficacy data", "Revenue guidance for another program",
])
def test_near_matches_placebo_and_unrelated_guidance_rejected(statement):
    assert candidate_matches_statement(registered_candidate(), statement)[0] is False


@pytest.mark.parametrize("identity", ["FOCUS-3", "ABC-123", "ABC123", "Specificmab"])
def test_registered_program_intervention_and_alias(identity):
    result = candidate_matches_statement(registered_candidate(), f"Topline data for ({identity.lower()}) in December.")
    assert result[0] and result[1] == .90
    assert result[2]  # Deterministic explanation retains the registered identity.


@pytest.mark.parametrize("name", [
    "Safety", "Efficacy", "Safety efficacy evaluation", "Randomized double blind study",
    "Placebo", "Placebo oral tablet", "Standard of care", "Best available therapy", "Chemotherapy",
    "ADULTS", "Healthy volunteers", "Pediatric participants", "Adult male patients",
    "ABC-123 placebo", "Specificmab placebo",
])
def test_generic_registered_fields_are_not_specific_identity(name):
    raw = {"identificationModule": {"acronym": name},
           "armsInterventionsModule": {"interventions": [{"name": name, "otherNames": [name]}]}}
    c = registered_candidate(raw_json=json.dumps(raw))
    assert candidate_matches_statement(c, f"{name} in December 2026")[0] is False


def test_title_only_drug_guess_does_not_verify():
    c = registered_candidate(title="ABC-123 Safety efficacy", raw_json="{}")
    assert not candidate_matches_statement(c, "ABC-123 safety efficacy in December")[0]


def test_placebo_alias_is_not_program_identity():
    raw = {"armsInterventionsModule": {"interventions": [
        {"name": "Placebo oral tablet", "otherNames": ["Control-X"]}]}}
    c = registered_candidate(raw_json=json.dumps(raw))
    assert not candidate_matches_statement(c, "Control-X data in December")[0]


def test_placebo_alias_of_active_intervention_does_not_verify():
    raw = {"armsInterventionsModule": {"interventions": [
        {"name": "ABC-123", "otherNames": ["ABC-123 placebo", "Healthy volunteers"]}]}}
    c = registered_candidate(raw_json=json.dumps(raw))
    # An alias carrying the control qualifier cannot itself be identity evidence.
    assert not candidate_matches_statement(c, "Healthy volunteers data in December")[0]
    from mozes.promotion import _terms
    assert _terms(c) == ["ABC-123"]


def test_demographic_registry_terms_remain_rejected_without_side_effects(conn):
    c = registered_candidate(raw_json=json.dumps({"identificationModule": {"acronym": "ADULTS"}}))
    statement = primary_statement("Healthy adults data in December 2026.")
    assert promote_candidate(conn, c, statement, "ZZZZ") is None
    assert promote_candidate(conn, c, statement, "ZZZZ") is None
    for table in ("events", "event_state", "event_sources", "change_events", "alert_outbox"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM v2e2_promotion_decisions").fetchone()[0] == 1


def test_exact_nct_still_matches_demographic_only_registry():
    c = registered_candidate(raw_json=json.dumps({"identificationModule": {"acronym": "ADULTS"}}))
    assert candidate_matches_statement(c, "NCT12345678 adult data in December")[:2] == (True, 1.0)


def test_registered_multiword_identity_normalizes_whitespace():
    raw = {"armsInterventionsModule": {"interventions": [{"name": "Drug X"}]}}
    c = registered_candidate(raw_json=json.dumps(raw))
    assert candidate_matches_statement(c, "Drug   X data in December")[:2] == (True, .90)


@pytest.mark.parametrize("text", [
    "ABC-123 results from NCT87654321 in December.",
    "NCT12345678 and NCT87654321: results expected in December.",
])
def test_conflicting_or_multiple_trial_identifiers_need_review(text):
    assert candidate_matches_statement(registered_candidate(), text)[0] is False


@pytest.mark.parametrize("raw", ["null", "[]", "invalid JSON", '{"protocolSection": null}'])
def test_unusable_registry_identity_fails_closed(raw):
    assert candidate_matches_statement(registered_candidate(raw_json=raw), "ABC-123 data")[0] is False


@pytest.mark.parametrize("identity,confidence", [("NCT12345678", 100), ("FOCUS-3", 99), ("Specificmab", 99)])
def test_valid_promotion_is_stable_and_idempotent(conn, identity, confidence):
    c = registered_candidate()
    statement = primary_statement(f"Novel Bio expects {identity} topline data in December 2026.")
    eid = promote_candidate(conn, c, statement, "ZZZZ")
    assert eid == "AUTO-ZZZZ-NCT12345678"
    state = db.event_state(conn, eid)
    assert state["verification_state"] == "VERIFIED" and state["verification_confidence"] == confidence
    before = {table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")] for table in
              ("events", "change_events", "alert_outbox", "event_sources")}
    assert promote_candidate(conn, c, statement, "ZZZZ") == eid
    for table, rows in before.items():
        assert [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")] == rows
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0] <= 1


def test_nonprimary_source_still_cannot_promote(conn):
    assert promote_candidate(conn, registered_candidate(), primary_statement("NCT12345678 data"),
                             "ZZZZ", source_type="news_search") is None
    assert conn.execute("SELECT reason FROM v2e2_promotion_decisions").fetchone()[0] == "non_primary_source"


def test_rejected_new_evidence_preserves_existing_event_and_receipts(conn):
    c = registered_candidate()
    eid = promote_candidate(conn, c, primary_statement("NCT12345678 data in December 2026."), "ZZZZ")
    change_id = conn.execute("SELECT change_id FROM change_events").fetchone()[0]
    with conn:
        conn.execute("INSERT INTO alert_outbox(alert_id,change_id,stage,channel,payload_json,status,attempts,created_at,send_after,sent_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     ("prior-receipt", change_id, "stage1", "browser", "{}", "sent", 1,
                      "2026-10-05", "2026-10-05", "2026-10-05"))
        conn.execute("INSERT INTO edge_event_links VALUES(?,?,?,?,?)",
                     ("prior-edge", change_id, "prior-run", "2026-10-05", "prior-source-hash"))
    tables = ("events", "event_state", "event_sources", "change_events", "alert_outbox", "edge_event_links")
    before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
    assert promote_candidate(conn, c, primary_statement("Safety efficacy for unrelated programs in December 2026."), "ZZZZ") is None
    assert eid == "AUTO-ZZZZ-NCT12345678"
    for table, rows in before.items():
        assert [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")] == rows
    assert promote_candidate(conn, c, primary_statement("NCT12345678 data in December 2026."), "ZZZZ") == eid
    for table, rows in before.items():
        assert [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")] == rows


@pytest.mark.parametrize('retrieval', ['2026-10-05T12:00:00Z', None])
def test_identical_promotion_is_no_write_across_distinct_instants(conn, monkeypatch, retrieval):
    clock = ['2026-10-13T12:00:00+00:00']
    monkeypatch.setattr(db, 'utcnow', lambda: clock[0])
    statement = primary_statement('NCT12345678 data in December 2026.')
    statement['retrieved_at'] = retrieval
    candidate = registered_candidate(sponsor='Novel Bio')
    event_id = promote_candidate(conn, candidate, statement, 'ZZZZ')
    change_id = conn.execute('SELECT change_id FROM change_events').fetchone()[0]
    with conn:
        conn.execute("INSERT INTO edge_event_links VALUES(?,?,?,?,?)",
                     ('immutable-edge', change_id, 'receipt-run', clock[0], 'immutable-source-hash'))
        conn.execute("UPDATE alert_outbox SET status='sent',sent_at=?,attempts=1", (clock[0],))
        conn.execute("UPDATE event_state SET event_timestamp=?,event_session='PRE_MARKET',resolved_at=?",
                     ('2026-12-01T08:00:00Z', '2026-10-01T00:00:00Z'))
        conn.execute("UPDATE watch_universe SET cik='000123'")
    before = list(conn.iterdump())
    changes = conn.total_changes
    clock[0] = '2026-10-13T12:00:02+00:00'
    sql = []
    conn.set_trace_callback(sql.append)
    assert promote_candidate(conn, candidate, statement, 'ZZZZ') == event_id
    conn.set_trace_callback(None)
    assert list(conn.iterdump()) == before
    assert conn.total_changes == changes
    assert not any(command.lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE', 'REPLACE'} for command in sql)


@pytest.mark.parametrize('field,value', [('status', 'CANDIDATE'), ('verification_state', 'QUARANTINED'),
                                        ('verification_confidence', 50), ('note', None)])
def test_changed_effective_verification_state_is_not_suppressed(conn, monkeypatch, field, value):
    clock = ['2026-10-13T12:00:00+00:00']
    monkeypatch.setattr(db, 'utcnow', lambda: clock[0])
    candidate = registered_candidate()
    statement = primary_statement('NCT12345678 data in December 2026.')
    event_id = promote_candidate(conn, candidate, statement, 'ZZZZ')
    expected = db.event_state(conn, event_id)[field]
    with conn:
        conn.execute(f'UPDATE event_state SET {field}=? WHERE event_id=?', (value, event_id))
    clock[0] = '2026-10-13T12:00:02+00:00'
    assert promote_candidate(conn, candidate, statement, 'ZZZZ') == event_id
    state = db.event_state(conn, event_id)
    assert state[field] == expected and state['updated_at'] == clock[0]


def test_rejected_evidence_preserves_records_and_repeated_rejection_is_stable(conn, monkeypatch):
    clock = ['2026-10-13T12:00:00+00:00']
    monkeypatch.setattr(db, 'utcnow', lambda: clock[0])
    candidate = registered_candidate(sponsor='Novel Bio')
    promote_candidate(conn, candidate, primary_statement('NCT12345678 data in December 2026.'), 'ZZZZ')
    tables = ('events', 'event_state', 'event_sources', 'change_events', 'alert_outbox', 'edge_event_links', 'watch_universe')
    before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in tables}
    rejected = primary_statement('Unrelated safety efficacy programs in December 2026.')
    clock[0] = '2026-10-13T12:00:02+00:00'
    assert promote_candidate(conn, candidate, rejected, 'ZZZZ') is None
    for table, rows in before.items():
        assert [tuple(r) for r in conn.execute(f'SELECT * FROM {table}')] == rows
    # First rejection keeps its existing audit contract; repeated evidence adds nothing.
    audit = list(conn.iterdump())
    clock[0] = '2026-10-13T12:00:04+00:00'
    assert promote_candidate(conn, candidate, rejected, 'ZZZZ') is None
    assert list(conn.iterdump()) == audit


def test_new_verified_guidance_updates_state_and_preserves_old_receipts(conn, monkeypatch):
    clock = ['2026-10-13T12:00:00+00:00']
    monkeypatch.setattr(db, 'utcnow', lambda: clock[0])
    candidate = registered_candidate(sponsor='Novel Bio')
    statement = primary_statement('NCT12345678 data in December 2026.')
    event_id = promote_candidate(conn, candidate, statement, 'ZZZZ')
    with conn:
        conn.execute("UPDATE alert_outbox SET status='sent',sent_at=?,attempts=1", (clock[0],))
    receipts = [tuple(r) for r in conn.execute('SELECT * FROM alert_outbox')]
    sources = [tuple(r) for r in conn.execute('SELECT * FROM event_sources')]
    clock[0] = '2026-10-13T12:00:02+00:00'
    changed = {**statement, 'statement': 'NCT12345678 data on December 15, 2026.',
               'published_at': '2026-10-06', 'retrieved_at': clock[0],
               'window': {'start': '2026-12-15', 'end': '2026-12-15', 'precision': 'exact', 'original': 'December 15'}}
    assert promote_candidate(conn, candidate, changed, 'ZZZZ') == event_id
    state = db.event_state(conn, event_id)
    assert state['status'] == 'SCHEDULED' and state['updated_at'] == clock[0]
    for row in receipts:
        assert tuple(conn.execute('SELECT * FROM alert_outbox WHERE alert_id=?', (row[0],)).fetchone()) == row
    for row in sources:
        assert row in [tuple(r) for r in conn.execute('SELECT * FROM event_sources')]
    before = list(conn.iterdump())
    clock[0] = '2026-10-13T12:00:04+00:00'
    assert promote_candidate(conn, candidate, changed, 'ZZZZ') == event_id
    assert list(conn.iterdump()) == before
