import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from mozes import db
from mozes.coverage import audit_coverage
from mozes.discovery import discover, store_candidates, study_to_candidate
from mozes.entity_resolution import update_company_map
from mozes.extract import extract_catalyst_statements
from mozes.market import pre_event_prices, event_return
from mozes.paper import PaperBook
from mozes.payload_v3 import build
from mozes.promotion import promote_candidate
from mozes.radar import bootstrap_database, validation_status
from mozes.source_observability import capture, record
from mozes.timing import parse_trigger
from mozes.validation_evaluator import evaluate_walk_forward

ROOT = Path(__file__).resolve().parents[1]
FIX = json.loads((ROOT / "tests/fixtures/sellas_v2e.json").read_text())


@pytest.mark.parametrize("text,kind", [
    ("Phase 3 final analysis after the 80th event", "EVENT_COUNT"),
    ("Topline upon the 80th death", "DEATH_COUNT"),
    ("Readout once enrollment is complete", "ENROLLMENT_COMPLETE"),
    ("Topline after database lock", "DATABASE_LOCK"),
    ("Topline following DSMB review", "DSMB_REVIEW"),
    ("Topline following final analysis", "FINAL_ANALYSIS"),
    ("Unblinding after target event count", "EVENT_COUNT"),
])
def test_trigger_extraction_never_invents_date(text, kind):
    item = extract_catalyst_statements(text, date(2026, 10, 1))[0]
    assert item["timing_mode"] == "EVENT_DRIVEN"
    assert item["trigger_type"] == kind
    assert item["window"]["start"] is item["window"]["end"] is None


def test_progress_parser():
    trigger = parse_trigger("REGAL final analysis after the 80th event; 78 events have occurred")
    assert (trigger["trigger_current"], trigger["trigger_target"], trigger["monitoring_state"]) == (78, 80, "NEAR_TRIGGER")


def sellas(conn):
    bootstrap_database(conn)
    identity = FIX["identity"]
    update_company_map(conn, [identity["sec_row"]], [identity["listing_row"]])
    db.upsert_security_lifecycle(conn, "SLS", status="ACTIVE", source_type="sec", source_url=identity["sec_url"])
    maps = [dict(row) for row in conn.execute("SELECT * FROM sponsor_ticker_map")]
    events = []
    for name in ("regal", "sls009"):
        fixture = FIX[name]
        # Synthetic registry envelope; only the primary quotations verify the catalyst.
        study = {"protocolSection": {"identificationModule": {"nctId": "FIXTURE-" + name, "briefTitle": fixture["program"], "acronym": fixture["program"]},
                 "sponsorCollaboratorsModule": {"leadSponsor": {"name": "SELLAS Life Sciences Group, Inc."}},
                 "designModule": {"phases": [fixture["phase"]]}, "statusModule": {"overallStatus": "ACTIVE_NOT_RECRUITING"}}}
        candidate = study_to_candidate(study, maps)
        assert candidate["ticker"] == "SLS"
        store_candidates(conn, [candidate])
        candidate = dict(conn.execute("SELECT * FROM discovery_candidates WHERE candidate_id=?", (candidate["candidate_id"],)).fetchone())
        text = fixture["quote"]
        if name == "regal":
            # Combine archived spans; framing date is normalized fixture metadata.
            text += ". As of May 11, 2026, " + fixture["progress_quote"] + "."
        statement = extract_catalyst_statements(text, date.fromisoformat(fixture["published_at"]), fixture["url"])[0]
        statement["retrieved_at"] = FIX["retrieved_at"]
        eid = promote_candidate(conn, candidate, statement, "SLS", source_type="company_ir")
        assert eid
        events.append(eid)
        before = conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0]
        assert promote_candidate(conn, candidate, statement, "SLS", source_type="company_ir") == eid
        assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == before
    return events


def test_sellas_two_distinct_catalysts_and_provenance(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    ids = sellas(conn)
    payload = build(conn, date(2026, 10, 1))
    rows = [row for row in payload["live"] if row["id"] in ids]
    assert len(rows) == 2
    event = next(row for row in rows if row["timing_mode"] == "EVENT_DRIVEN")
    assert event["days_to"] is None and event["date"]["window"] is None
    assert event["trigger_current"] == 78 and event["trigger_target"] == 80
    assert event["trigger_as_of"] == "2026-05-11"
    calendar = next(row for row in rows if row["timing_mode"] == "CALENDAR")
    assert calendar["date"]["window"]["start"] == "2026-10-01"
    assert calendar["date"]["window"]["end"] == "2026-12-31"
    for row in rows:
        assert set(ids) <= {x["id"] for x in row["catalyst_chain"]}
        assert row["provenance"]["primary"] is True
        assert row["state"]["event_session"] == "unknown"
    coverage = audit_coverage(conn)
    assert coverage["event_driven_count"] == 1
    gates = validation_status(conn)
    assert not gates["runup"]["enabled"] and not gates["hold_through"]["enabled"]


def test_no_production_ticker_special_case():
    for path in (ROOT / "mozes").glob("*.py"):
        assert "SLS" not in path.read_text(encoding="utf-8"), path


def test_discovery_queries_past_completion_and_bounds_requests():
    calls = []
    def fetch(url):
        calls.append(url)
        return {"studies": [{"protocolSection": {"identificationModule": {"nctId": "NCT00000001"}}}], "nextPageToken": "more"}
    rows, meta = discover(fetcher=fetch, today=date(2026, 10, 1), max_pages=2, return_metadata=True)
    assert len(calls) == 2 and len(rows) == 1
    assert meta["stop_reason"] == "page_cap" and meta["resume_cursor"]["window_index"] == 0
    assert "2024-10-01" not in calls[-1]


def test_discovery_finds_candidate_after_page_three_and_reports_bounded_stop():
    calls = []
    def fetch(url):
        calls.append(url)
        page = len(calls)
        study = {"protocolSection": {"identificationModule": {"nctId": f"NCT0000000{page}",
                    "briefTitle": "SELLAS later-page study"},
                    "statusModule": {"overallStatus": "RECRUITING"}}}
        return {"studies": [study], "nextPageToken": f"page-{page}" if page < 4 else None}
    rows, meta = discover(fetcher=fetch, today=date(2026, 10, 1), max_pages=5, return_metadata=True)
    assert len(calls) == 5  # four forward pages plus the lookback first page
    assert any(row["nct_id"] == "NCT00000004" for row in rows)
    assert meta["pages_fetched"] == 5 and meta["candidates_seen"] == 5
    assert meta["truncated"] is False and meta["stop_reason"] == "complete"


def test_coverage_full_reconciliation_is_not_partial_over_1000_rows(tmp_path):
    conn = db.connect(tmp_path / "large-coverage.db")
    store_candidates(conn, [study_to_candidate({"NCTId": f"NCT{i:08d}"}) for i in range(1001)])
    result = audit_coverage(conn)
    assert result["total_candidates"] == result["scanned_candidates"] == 1001
    assert result["truncated"] is False and result["stop_reason"] == "complete"
    assert conn.execute("SELECT status FROM v2c_operation_runs WHERE operation='coverage_audit' ORDER BY run_id DESC LIMIT 1").fetchone()[0] == "OK"


def test_paper_idempotency_chain_and_tamper(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    book = PaperBook(conn)
    row = {"id": "A", "as_of": "2026-10-01", "sources": [{"url": "https://example.test"}]}
    first = book.record(row, idempotency_key="one")
    assert book.record(row, idempotency_key="one") == first
    book.record({**row, "id": "B"}, idempotency_key="two")
    assert book.verify()["valid"]
    with pytest.raises(ValueError):
        book.record({**row, "as_of": "2026-10-02"}, idempotency_key="one")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE paper_audit SET input_hash='x'")
    conn.rollback()
    conn.execute("DROP TRIGGER trg_paper_no_update")
    conn.execute("UPDATE paper_signals SET payload='{}' WHERE signal_id=?", (first,))
    assert not book.verify()["valid"]


def test_session_boundaries_exclude_contaminated_close():
    rows = [{"date": "2026-01-02", "close": 10}, {"date": "2026-01-05", "close": 15}, {"date": "2026-01-06", "close": 30}]
    assert len(pre_event_prices(rows, "2026-01-05", "afterhours")) == 2
    assert len(pre_event_prices(rows, "2026-01-05", "premarket")) == 1
    assert pre_event_prices(rows, "2026-01-05", "unknown") == []
    assert event_return(rows, "2026-01-05", "afterhours")["return"] == 1


def test_failure_and_empty_success_are_distinct():
    with capture() as metrics:
        record("sec", cache=False)
        record("clinicaltrials.gov", error=True)
    assert metrics.snapshot()["sources"]["sec"]["state"] == "OK"
    assert metrics.snapshot()["sources"]["clinicaltrials.gov"]["state"] == "FAILED"


def test_evaluator_preserves_enabled_and_cohort_contract(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    db.set_validation(conn, "runup", True, 0, 999)
    result = evaluate_walk_forward(conn)
    assert db.validation_row(conn, "runup")["enabled"] == 1
    assert db.validation_row(conn, "runup")["min_oos_n"] == 999
    assert set(result["runup"]["cohorts"]) >= {"P2_TOPLINE", "P3_TOPLINE", "PDUFA", "ADCOM"}
    assert not db.validation_row(conn, "hold_through")["enabled"]


def test_registry_date_revision_retains_identity(tmp_path):
    conn = db.connect(tmp_path / 'registry.db')
    candidate = study_to_candidate({'NCTId': 'NCT001', 'PrimaryCompletionDate': '2025-01-01'})
    store_candidates(conn, [candidate])
    revised = study_to_candidate({'NCTId': 'NCT001', 'PrimaryCompletionDate': '2026-01-01'})
    store_candidates(conn, [revised])
    rows = conn.execute('SELECT * FROM discovery_candidates').fetchall()
    assert len(rows) == 1 and rows[0]['candidate_id'] == candidate['candidate_id']
    assert rows[0]['primary_completion'] == '2026-01-01'


def test_registry_cache_hit_and_failed_fetch_are_visible(tmp_path, monkeypatch):
    from mozes import discovery
    monkeypatch.setenv('MOZES_HTTP_CACHE', str(tmp_path))
    calls = []
    def fetch(url):
        calls.append(url)
        record('clinicaltrials.gov', cache=False)
        return {'studies': []}
    monkeypatch.setattr(discovery, 'fetch_json', fetch)
    with capture() as metrics:
        assert discovery.cached_fetch_json('https://example.test') == {'studies': []}
        assert discovery.cached_fetch_json('https://example.test') == {'studies': []}
    assert len(calls) == 1
    source = metrics.snapshot()['sources']['clinicaltrials.gov']
    assert source['cache_hits'] == 1 and source['cache_misses'] == 1 and source['errors'] == 0


@pytest.mark.parametrize('stamp,session', [
    ('2026-01-05T14:00:00Z', 'premarket'),
    ('2026-07-06T14:00:00Z', 'intraday'),
    ('2026-01-05T21:01:00Z', 'afterhours'),
    ('2026-07-06T20:01:00Z', 'afterhours'),
    ('2026-07-06', 'unknown'), ('2026-07-06T14:00:00', 'unknown'),
])
def test_publication_sessions_respect_dst_and_unknown(stamp, session):
    from mozes.session import session_from_publication
    assert session_from_publication(stamp) == session


def test_coverage_surfaces_unmapped_and_unbound_primary_evidence(tmp_path):
    conn = db.connect(tmp_path / 'coverage.db')
    store_candidates(conn, [study_to_candidate({'NCTId': 'NCT001'})])
    conn.execute('INSERT INTO catalyst_evidence VALUES(?,?,?,?,?,?)',
                 ('e', 'XYZ', 'https://issuer.test', '2026-01-01', '2026-01-01', json.dumps({'statement': 'Topline drug Q expected in Q4 2026'})))
    result = audit_coverage(conn)
    assert result['unmapped_count'] == 1 and result['missing_count'] == 2
    assert result['review_required_count'] == 1
    assert conn.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


def test_forward_queue_is_idempotent_and_immutable(tmp_path):
    conn = db.connect(tmp_path / 'forward.db')
    bootstrap_database(conn)
    row = {'id': 'XYZ', 'state': {'verification_state': 'VERIFIED'},
           'classification': {'class': 'REVIEW'}, 'evidence': {'score': 65}, 'impact': {'score': 70}}
    book = PaperBook(conn)
    assert book.queue_candidates([row]) == 1
    assert book.queue_candidates([{**row, 'impact': {'score': 90}}]) == 0
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE forward_candidates SET event_id='changed'")
    conn.rollback()
    assert not db.validation_row(conn, 'runup')['enabled']


def test_price_failure_does_not_block_monitor(tmp_path, monkeypatch):
    from mozes import pipeline_v2c as pipeline, live_prices, live_monitor
    conn = db.connect(tmp_path / 'pipeline.db')
    bootstrap_database(conn)
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    monkeypatch.setattr(pipeline, 'reconcile_catalog', lambda conn: {})
    monkeypatch.setattr(pipeline, 'import_local_research', lambda conn: {})
    monkeypatch.setattr(pipeline, 'identity_audit', lambda conn: {'status': 'OK'})
    def fail(conn):
        raise TimeoutError('unavailable')
    monkeypatch.setattr(live_prices, 'refresh_live_prices', fail)
    calls = []
    monkeypatch.setattr(live_monitor, 'run_monitor', lambda conn, **kw: calls.append(kw) or {'status': 'OK'})
    result = pipeline.run_pipeline(conn)
    assert calls and result['status'] == 'PARTIAL'
    assert result['details']['prices']['status'] == 'FAILED'


def test_static_ui_provenance_and_disabled_refresh():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes={apiLabel:{},refreshBtn:{}};
const ctx={days:()=>'',win:()=>'',focusCard:()=>'<article></article>',render:()=>{},
 dashboard:()=>'<main/>',changedCompact:()=>'<changes/>',changeDescription:()=>'',
 sources:()=>'',reasonLabel:x=>x,apiMode:false,
 esc:x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 document:{querySelectorAll:()=>[],getElementById:id=>nodes[id]}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);
ctx.render();assert(nodes.refreshBtn.disabled);assert(nodes.apiLabel.textContent);
const card=ctx.focusCard({provenance:{quote:'<script>bad</script>',source_url:'javascript:bad'}});
assert(card.includes('&lt;script&gt;'));assert(!card.includes('href='));
assert(ctx.dashboard().startsWith('<changes/>'));
assert(ctx.days({timing_mode:'EVENT_DRIVEN',trigger_current:78,trigger_target:80}).includes('78/80'));
'''
    subprocess.run([node, '-e', script, str(ROOT / 'web/coverage_ui.js')], check=True, timeout=15)


def test_abnormal_return_estimation_does_not_use_outcome():
    from datetime import timedelta
    from mozes.market import abnormal_return
    stock, bench = [], []
    s, b = 100.0, 100.0
    for i in range(42):
        r = .001 * (1 if i % 2 else -1)
        s *= 1 + 2 * r
        b *= 1 + r
        day = (date(2026, 1, 1) + timedelta(days=i)).isoformat()
        stock.append({'date': day, 'close': s})
        bench.append({'date': day, 'close': b})
    event_day = stock[-2]['date']
    before = abnormal_return(stock, bench, event_day, 'afterhours')
    stock[-1]['close'] *= 1.5
    after = abnormal_return(stock, bench, event_day, 'afterhours')
    assert before['beta'] == pytest.approx(2)
    assert after['beta'] == before['beta']
    assert after['car'] > before['car']
    assert not abnormal_return(stock, bench, event_day, 'unknown')['available']


def test_primary_publication_beats_sec_clock_on_import(tmp_path):
    from mozes.historical import import_bundle
    conn = db.connect(tmp_path / 'timestamps.db')
    bundle = {'sources': [{'source_id': 'release', 'canonical_url': 'https://issuer.test/result',
                          'source_type': 'company_ir', 'published_at': '2026-01-05T13:00:00Z', 'content': 'Result'}],
              'cases': [{'case_id': 'case', 'ticker': 'XYZ', 'catalyst_type': 'P3_TOPLINE',
                         'event_at': '2026-01-05T22:00:00Z', 'timestamp_source': 'sec_acceptance',
                         'primary_publication_at': '2026-01-05T13:00:00Z',
                         'announcement_session': 'afterhours', 'source_ids': ['release']}]}
    path = tmp_path / 'bundle.json'
    path.write_text(json.dumps(bundle))
    import_bundle(conn, path)
    case = db.historical_case_rows(conn)[0]
    assert case['announcement_session'] == 'premarket'
    assert case['event_at'] == '2026-01-05T13:00:00Z'


def test_event_driven_never_opens_candidate_classification_even_enabled_gates():
    from mozes.engine_v2 import classify_v2
    result = classify_v2({'timing_mode': 'EVENT_DRIVEN'}, {'status': 'VERIFIED'},
                         {'score': 99}, {'score': 99}, [], {'available': True}, 100,
                         {'runup': {'satisfied': True}, 'hold_through': {'satisfied': True}},
                         '2026-10-01', None)
    assert result['class'] == 'REVIEW' and result['actionable'] is False


def test_zero_event_target_is_not_a_valid_trigger():
    assert parse_trigger('Final analysis after 0 events; 0 events have occurred') is None


@pytest.mark.parametrize('body', [[], 'text', 42, None])
def test_api_rejects_non_object_body(body):
    from mozes.app_server import Handler
    handler = object.__new__(Handler)
    handler.path = '/api/paper'
    handler._body_json = lambda: body
    replies = []
    handler._send_json = lambda value, status: replies.append((value, status))
    handler.do_POST()
    assert replies[0][1] == 400


def test_api_rejects_backdated_current_inputs(monkeypatch):
    from mozes import app_server
    from types import SimpleNamespace
    handler = object.__new__(app_server.Handler)
    handler.path = '/api/paper'
    handler.server = SimpleNamespace(conn=None)
    handler._body_json = lambda: {'event_id': 'XYZ', 'as_of': '2000-01-01'}
    monkeypatch.setattr(app_server, '_find_live_event', lambda *args: {'id': 'XYZ'})
    replies = []
    handler._send_json = lambda value, status: replies.append((value, status))
    handler.do_POST()
    assert replies[0][1] == 400
