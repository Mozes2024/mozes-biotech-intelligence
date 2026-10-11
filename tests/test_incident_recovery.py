"""Incident scheduling must not turn a stale checkpoint into a reusable bypass."""
import importlib.util
import json
import sys
import threading
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from mozes import monitor_recovery


def canonical_events():
    return [{"event_id": f"EDGE-{n:024x}", "source": "businesswire", "ticker": "BIO",
        "cik": "0000000001", "form": None, "accession": None,
        "source_url": f"https://www.businesswire.com/news/home/{n}", "filing_index_url": None,
        "first_seen_at": "2026-10-10T12:00:00Z", "accepted_at": None, "published_at": "source-date",
        "enrichment_ack_at": "2026-10-10T12:01:00Z" if n < 8 else None,
        "enrichment_completed_at": "2026-10-10T12:00:50Z" if n < 8 else None,
        "enrichment_change_id": f"CHG-{n:024x}" if n < 8 else None,
        "enrichment_github_run_id": "100" if n < 8 else None,
        "enrichment_alert_ids_json": '["alert"]' if n < 8 else None,
        "enrichment_delivery_json": '[{"status":"sent","channel":"browser"}]' if n < 8 else None,
        "enrichment_queued_at": None, "enrichment_dispatch_at": "2026-10-11T00:55:00Z", "analysis_completed_at": "2026-10-10T12:00:00Z"}
        for n in range(27)]


@pytest.mark.parametrize("field,value", [("event_id", "EDGE-ffffffffffffffffffffffff"),
    ("source_url", "https://www.businesswire.com/news/replaced"), ("ticker", "OTHER"),
    ("enrichment_change_id", "CHG-ffffffffffffffffffffffff"),
    ("enrichment_delivery_json", '[{"status":"sent","channel":"other"}]')])
def test_complete_edge_fingerprints_detect_same_count_substitution(gate, field, value):
    rows = canonical_events()
    expected = gate.edge_fingerprints(rows)
    changed = copy.deepcopy(rows)
    changed[0][field] = value
    assert len(changed) == 27 and sum(bool(row["enrichment_ack_at"]) for row in changed) == 8
    assert gate.edge_fingerprints(changed) != expected


@pytest.fixture
def gate(monkeypatch, tmp_path):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("incident", scripts / "incident_recovery.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setenv("GITHUB_REPOSITORY", m.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("GITHUB_RUN_ID", "900")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("MOZES_INCIDENT_APPROVED_MAIN", "a" * 40)
    monkeypatch.setenv("MOZES_DB_PATH", str(tmp_path / "live.db"))
    for key in ("MOZES_NTFY_URL", "MOZES_WEBHOOK_URL", "MOZES_SMTP_USER", "MOZES_SMTP_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    return m


def ready(m, monkeypatch, *, capacity=True):
    monkeypatch.setattr(m.restore, "_list_success_runs", lambda: [{"databaseId": m.SOURCE}])
    monkeypatch.setattr(m, "provider_gate", lambda _: {"capacity_demonstrated": capacity})
    monkeypatch.setattr(m, "pending_gate", lambda: None)
    monkeypatch.setattr(m, "public_feed", lambda: {"revision":
        "246b6cd8797afa35a7986eba94f102d1f0975b552bd4af6dfc9b9f529587b73c", "alerts": [None] * 40})
    restores = []
    def restore(*a, **kwargs):
        restores.append(kwargs)
        if not kwargs.get("verify_only"):
            import os
            Path(os.environ["MOZES_DB_PATH"]).with_name("recovery-proof.json").write_text('{}')
        return True
    monkeypatch.setattr(m.restore, "restore", restore)
    state = {"ref": None, "record": None, "posts": []}
    lock = threading.Lock()

    def api(path, *, body=None, absent_ok=False):
        with lock:
            if path == "commits/main":
                return {"sha": "a" * 40}
            if path.startswith("actions/workflows/ci.yml"):
                return {"workflow_runs": [{"head_sha": "a" * 40, "head_branch": "main",
                        "event": "push", "status": "completed", "conclusion": "success"}]}
            if path.startswith("actions/artifacts"):
                return {"expired": False, "workflow_run": {"id": int(m.SOURCE)},
                    "digest": "sha256:8157f00682d5c415f6fca51e7d6341b714cd9d52fdf5da80b51be6e480f6c447"}
            if path.startswith("actions/runs"):
                return {"run_attempt": 1, "conclusion": "success"}
            if path.startswith("git/ref/"):
                return state["ref"]
            if path == "git/commits/" + "a" * 40:
                return {"sha": "a" * 40, "tree": {"sha": "tree"}}
            if path == "git/commits":
                state["record"] = body["message"]
                state["posts"].append(path)
                return {"sha": "claim"}
            if path == "git/commits/claim":
                return {"message": state["record"]}
            if path == "git/refs":
                state["posts"].append(path)
                if state["ref"]:
                    raise RuntimeError("atomic ref conflict")
                state["ref"] = {"ref": body["ref"], "object": {"sha": body["sha"]}}
                return state["ref"]
            raise AssertionError(path)
    monkeypatch.setattr(m, "api", api)
    return state, restores


def test_success_claim_repeated_preflight_and_owner_restore(gate, monkeypatch):
    state, restores = ready(gate, monkeypatch)
    now = gate.START + timedelta(minutes=20)
    assert gate.eligible(now) == "recover"
    assert gate.claim(now) == "claim"
    gate.process(now)
    assert sum(not r.get("verify_only") for r in restores) == 1
    assert state["posts"].count("git/refs") == 1
    with pytest.raises(RuntimeError, match="claim exists"):
        gate.claim(now)
    assert state["posts"].count("git/refs") == 1


def test_missing_capacity_waits_without_claim_or_restore(gate, monkeypatch):
    state, restores = ready(gate, monkeypatch, capacity=False)
    assert gate.eligible(gate.START) == "wait"
    assert not state["posts"] and not restores
    with pytest.raises(RuntimeError):
        gate.claim(gate.START)
    assert not state["posts"]


def test_before_start_and_newer_artifact_never_recover(gate, monkeypatch):
    state, restores = ready(gate, monkeypatch)
    assert gate.eligible(gate.START - timedelta(seconds=1)) == "wait"
    monkeypatch.setattr(gate.restore, "_list_success_runs", lambda: [{"databaseId": "newer"}])
    assert gate.eligible(gate.START) == "ordinary"
    assert not state["posts"] and not restores


@pytest.mark.parametrize("env,value", [("GITHUB_RUN_ATTEMPT", "2"),
    ("GITHUB_EVENT_NAME", "workflow_dispatch"), ("GITHUB_SHA", "b" * 40),
    ("GITHUB_REF", "refs/heads/other")])
def test_rerun_manual_or_changed_head_cannot_claim(gate, monkeypatch, env, value):
    state, restores = ready(gate, monkeypatch)
    monkeypatch.setenv(env, value)
    with pytest.raises(RuntimeError):
        gate.claim(gate.START)
    assert not state["posts"] and not restores


def test_expired_activation_window_is_not_shifted_by_schedule_delay(gate, monkeypatch):
    state, _ = ready(gate, monkeypatch)
    with pytest.raises(RuntimeError, match="window"):
        gate.claim(gate.END)
    assert not state["posts"]


@pytest.mark.parametrize("error", ["checksum mismatch", "newer artifact", "ambiguous provider delivery"])
def test_existing_restore_gate_failure_prevents_claim(gate, monkeypatch, error):
    state, _ = ready(gate, monkeypatch)
    def fail(*a, **k):
        raise RuntimeError(error)
    monkeypatch.setattr(gate.restore, "restore", fail)
    with pytest.raises(RuntimeError, match=error):
        gate.claim(gate.START)
    assert not state["posts"]


@pytest.mark.parametrize("route", ["schedule", "manual"])
def test_claim_race_has_one_winner(gate, monkeypatch, tmp_path, route):
    state, _ = ready(gate, monkeypatch)
    if route == "manual":
        manual_route(gate, monkeypatch, tmp_path)
    barrier = threading.Barrier(2)
    original = gate.eligible
    def eligible(now):
        value = original(now)
        barrier.wait(timeout=5)
        return value
    monkeypatch.setattr(gate, "eligible", eligible)
    def attempt(_):
        try:
            gate.claim(gate.START)
            return True
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1
    assert state["ref"]


def manual_route(gate, monkeypatch, tmp_path, **changes):
    inputs = {"incident_recovery_authorization": f"20261011:{gate.SOURCE}", "priority_only": "true"}
    inputs.update(changes)
    payload = tmp_path / "dispatch.json"
    payload.write_text(json.dumps({"inputs": inputs}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("MOZES_INCIDENT_MANUAL_APPROVAL",
        f"{'a' * 40}:workflow_dispatch:{gate.SOURCE}:{gate.SHA256}")


def test_manual_recovery_requires_separate_sha_bound_approval(gate, monkeypatch, tmp_path):
    state, restores = ready(gate, monkeypatch)
    manual_route(gate, monkeypatch, tmp_path)
    gate.claim(gate.START)
    gate.process(gate.START)
    assert state["ref"] and sum(not r.get("verify_only") for r in restores) == 1
    assert __import__("os").environ["GITHUB_EVENT_NAME"] == "workflow_dispatch"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    with pytest.raises(RuntimeError, match="claim exists"):
        gate.claim(gate.START)


@pytest.mark.parametrize("changes", [
    {"incident_recovery_authorization": ""}, {"incident_recovery_authorization": "wrong"},
    {"priority_only": "false"}, {"source_run_id": "38052910145"},
    {"recovery_checkpoint_sha256": "other"}, {"edge_event_id": "EDGE-other"},
    {"hot_ticker": "OTHER"}, {"hot_cik": "123"}, {"hot_accession": "other"},
    {"hot_source_url": "https://example.com"}])
def test_manual_input_cannot_expand_bounded_recovery(gate, monkeypatch, tmp_path, changes):
    state, restores = ready(gate, monkeypatch)
    manual_route(gate, monkeypatch, tmp_path, **changes)
    with pytest.raises(RuntimeError, match="unapproved"):
        gate.claim(gate.START)
    assert not state["posts"] and not restores


@pytest.mark.parametrize("approval", ["", "wrong", "b" * 40 + ":workflow_dispatch:38052910145"])
def test_manual_approval_missing_or_other_head_fails_closed(gate, monkeypatch, tmp_path, approval):
    state, _ = ready(gate, monkeypatch)
    manual_route(gate, monkeypatch, tmp_path)
    monkeypatch.setenv("MOZES_INCIDENT_MANUAL_APPROVAL", approval)
    with pytest.raises(RuntimeError, match="unapproved"):
        gate.claim(gate.START)
    assert not state["posts"]


def test_schedule_claim_cannot_be_consumed_again_by_manual_route(gate, monkeypatch, tmp_path):
    state, _ = ready(gate, monkeypatch)
    gate.claim(gate.START)
    manual_route(gate, monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "901")
    with pytest.raises(RuntimeError, match="claim exists"):
        gate.claim(gate.START)
    with pytest.raises(RuntimeError, match="another run"):
        gate.process(gate.START)
    assert state["posts"].count("git/refs") == 1


def test_manual_newer_checkpoint_never_falls_through_to_processing(gate, monkeypatch, tmp_path):
    ready(gate, monkeypatch)
    manual_route(gate, monkeypatch, tmp_path)
    monkeypatch.setattr(gate.restore, "_list_success_runs", lambda: [{"databaseId": "newer"}])
    with pytest.raises(RuntimeError, match="newer checkpoint closes"):
        gate.eligible(gate.START)


@pytest.mark.parametrize("mode", ["missing_capacity", "expired", "rerun", "bad_payload", "audit_failure"])
def test_manual_route_retains_original_fail_closed_gates(gate, monkeypatch, tmp_path, mode):
    state, restores = ready(gate, monkeypatch, capacity=mode != "missing_capacity")
    manual_route(gate, monkeypatch, tmp_path)
    now = gate.END if mode == "expired" else gate.START
    if mode == "rerun":
        monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    if mode == "bad_payload":
        Path(__import__("os").environ["GITHUB_EVENT_PATH"]).write_text('{broken')
    if mode == "audit_failure":
        monkeypatch.setattr(gate.restore, "restore", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("producer audit failed")))
    with pytest.raises(RuntimeError):
        gate.claim(now)
    assert not state["posts"] and not any(not r.get("verify_only") for r in restores)


@pytest.mark.parametrize("route", ["schedule", "manual"])
def test_crash_after_claim_cannot_be_adopted_by_next_run(gate, monkeypatch, tmp_path, route):
    _, restores = ready(gate, monkeypatch)
    if route == "manual":
        manual_route(gate, monkeypatch, tmp_path)
    gate.claim(gate.START)
    monkeypatch.setenv("GITHUB_RUN_ID", "901")
    with pytest.raises(RuntimeError, match="another run"):
        gate.process(gate.START)
    with pytest.raises(RuntimeError, match="claim exists"):
        gate.claim(gate.START)
    assert not any(not r.get("verify_only") for r in restores)


def test_lost_capacity_after_claim_preserves_latch_without_processing(gate, monkeypatch):
    state, restores = ready(gate, monkeypatch)
    gate.claim(gate.START)
    monkeypatch.setattr(gate, "provider_gate", lambda _: {"capacity_demonstrated": False})
    with pytest.raises(RuntimeError, match="capacity proof lost"):
        gate.process(gate.START)
    assert state["ref"] and not any(not r.get("verify_only") for r in restores)


@pytest.mark.parametrize("reply", [None, {"object": {"sha": "wrong"}}])
def test_unsuccessful_or_ambiguous_ref_creation_never_enables_processing(gate, monkeypatch, reply):
    state, restores = ready(gate, monkeypatch)
    original = gate.api
    calls = []
    def api(path, **kwargs):
        if path == "git/refs":
            calls.append(path)
            if reply is None:
                raise RuntimeError("HTTP result ambiguous")
            return reply
        return original(path, **kwargs)
    monkeypatch.setattr(gate, "api", api)
    with pytest.raises(RuntimeError):
        gate.claim(gate.START)
    assert calls == ["git/refs"]
    assert not any(not r.get("verify_only") for r in restores)


def test_new_feed_receipt_prevents_claim(gate, monkeypatch):
    state, _ = ready(gate, monkeypatch)
    monkeypatch.setattr(gate, "public_feed", lambda: {"revision": "new", "alerts": []})
    with pytest.raises(RuntimeError, match="feed changed"):
        gate.claim(gate.START)
    assert not state["posts"]


@pytest.mark.parametrize("key", ["MOZES_NTFY_URL", "MOZES_WEBHOOK_URL"])
def test_external_delivery_blocks_processing(gate, monkeypatch, key):
    _, restores = ready(gate, monkeypatch)
    gate.claim(gate.START)
    monkeypatch.setenv(key, "enabled")
    with pytest.raises(RuntimeError, match="external notification"):
        gate.process(gate.START)
    assert not any(not r.get("verify_only") for r in restores)


@pytest.mark.parametrize("writes,total,expected", [(0, 0, False), (1, 1, True), (0, 89999, False)])
def test_provider_capacity_requires_actual_current_hour_writes(gate, monkeypatch, writes, total, expected):
    now = gate.START + timedelta(hours=1)
    settings = {"test": "settings"}
    import hashlib
    monkeypatch.setattr(gate, "SETTINGS", hashlib.sha256(json.dumps(settings, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest())
    monkeypatch.setattr(gate, "BUNDLE", hashlib.sha256(b"bundle").hexdigest())
    events = canonical_events()
    monkeypatch.setattr(gate, "EDGE_FINGERPRINTS", gate.edge_fingerprints(events))
    def cf(path, **kwargs):
        if path.endswith("settings"):
            return settings
        if path.endswith("deployments"):
            return {"deployments": [{"created_on": "2026-10-10T12:29:11Z", "versions": [{"version_id": gate.WORKER, "percentage": 100}]}]}
        if path.endswith("schedules"):
            return {"schedules": [{"cron": "*/2 * * * *"}]}
        if path.endswith("content/v2"):
            return (b'--BOUND\r\nContent-Disposition: form-data; name="index.js"; filename="index.js"\r\n\r\nbundle\r\n--BOUND--\r\n',
                    'multipart/form-data; boundary="BOUND"')
        if path.endswith("query"):
            assert kwargs["body"] == {"sql": gate.D1_QUERY}
            return [{"success": True, "meta": {"rows_written": 0}, "results": [{"name": name} for name in
                ["0001_alert_feed.sql", "0002_hot_edge.sql", "0003_hot_edge_reliability.sql", "0004_clinical_candidates.sql"]]},
                {"success": True, "meta": {"rows_written": 0}, "results": events}]
        if path.endswith(gate.DATABASE):
            return {"uuid": gate.DATABASE, "name": "mozes-alerts", "created_at": "2026-10-01T00:00:00Z"}
        assert path == "graphql"
        return {"data": {"viewer": {"accounts": [{"d1AnalyticsAdaptiveGroups": [
            {"dimensions": {"databaseId": gate.DATABASE, "datetimeHour": "2026-10-11T01:00:00Z"},
             "sum": {"rowsWritten": writes}},
            {"dimensions": {"databaseId": "other-database", "datetimeHour": "2026-10-11T00:00:00Z"},
             "sum": {"rowsWritten": total - writes}}]}]}}}
    monkeypatch.setattr(gate, "cf", cf)
    assert gate.provider_gate(now)["capacity_demonstrated"] is expected


def test_missing_provider_credential_does_not_issue_request(gate, monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_RECOVERY_READ_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="CLOUDFLARE_RECOVERY_READ_TOKEN"):
        gate.cf("anything")


@pytest.mark.parametrize("kind", ["identity", "ack", "metadata", "missing_deployment", "future_deployment", "content"])
def test_provider_gate_rejects_corrupted_contract_or_history(gate, monkeypatch, kind):
    test_provider_capacity_requires_actual_current_hour_writes(gate, monkeypatch, 1, 1, True)
    original = gate.cf
    def corrupted(path, **kwargs):
        value = copy.deepcopy(original(path, **kwargs))
        if path.endswith("query"):
            if kind == "identity":
                value[1]["results"][0]["event_id"] = "EDGE-ffffffffffffffffffffffff"
            elif kind == "ack":
                value[1]["results"][0]["enrichment_change_id"] = "CHG-ffffffffffffffffffffffff"
        if path.endswith(gate.DATABASE) and kind == "metadata":
            value["uuid"] = "other-database"
        if path.endswith("deployments"):
            if kind == "missing_deployment":
                return {}
            if kind == "future_deployment":
                value["deployments"][0]["created_on"] = "2026-10-12T00:00:00Z"
        if path.endswith("content/v2") and kind == "content":
            return b"missing module", "text/plain"
        return value
    monkeypatch.setattr(gate, "cf", corrupted)
    with pytest.raises(gate.IncidentGateError):
        gate.provider_gate(gate.START + timedelta(hours=1))


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", None])
def test_prior_started_claim_blocks_retry_even_without_a_ref(conclusion):
    def gh(*args):
        if "workflows/" in args[1]:
            return json.dumps({"workflow_runs": [{"id": 901, "status": "completed",
                "updated_at": "2026-10-11T00:10:00Z", "run_attempt": 1, "head_branch": "main",
                "name": "lightweight-live-monitor", "head_repository": {"full_name": "Owner/Repo"}}]})
        return json.dumps({"total_count": 2, "jobs": [
            {"name": "incident-eligibility", "steps": [{"name": "Acquire irreversible incident recovery claim",
                                                       "conclusion": conclusion}]},
            {"name": "monitor", "conclusion": "skipped", "steps": []}]})
    with pytest.raises(RuntimeError, match="claim attempted"):
        monitor_recovery.audit_producers("Owner/Repo", gate_time(), gh, full_history=True)


def gate_time():
    from datetime import datetime, timezone
    return datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def test_waiting_gate_is_verified_no_write_producer(monkeypatch):
    monkeypatch.setenv("MOZES_INCIDENT_APPROVED_MAIN", "a" * 40)
    def gh(*args):
        if "workflows/" in args[1]:
            return json.dumps({"workflow_runs": [{"id": 901, "status": "completed",
                "updated_at": "2026-10-11T00:10:00Z", "run_attempt": 1, "head_branch": "main", "head_sha": "a" * 40,
                "name": "lightweight-live-monitor", "head_repository": {"full_name": "Owner/Repo"}}]})
        return json.dumps({"total_count": 2, "jobs": [
            {"name": "incident-eligibility", "steps": [
                {"name": "Verify incident recovery eligibility", "conclusion": "success"},
                {"name": "Acquire irreversible incident recovery claim", "conclusion": "skipped"}]},
            {"name": "monitor", "conclusion": "skipped", "steps": []}]})
    proof = monitor_recovery.audit_producers("Owner/Repo", gate_time(), gh, full_history=True)
    assert proof["audited_producers"] == ["901"]


@pytest.mark.parametrize("status", ["completed", "pending"])
def test_disabled_incident_job_does_not_break_ordinary_producer_audit(status):
    def gh(*args):
        if "workflows/" in args[1]:
            return json.dumps({"workflow_runs": [{"id": 901, "status": status,
                "updated_at": "2026-10-11T00:10:00Z", "run_attempt": 1, "head_branch": "main",
                "name": "lightweight-live-monitor", "head_repository": {"full_name": "Owner/Repo"}}]})
        if status == "pending":
            monitor = {"name": "monitor", "status": "queued", "steps": []}
        else:
            monitor = {"name": "monitor", "steps": [
                {"name": name, "conclusion": "skipped"} for name in monitor_recovery.DELIVERY_STEPS]
                + [{"name": "Enrich the correlated Edge event", "conclusion": "skipped"}]}
        return json.dumps({"total_count": 2, "jobs": [
            {"name": "incident-eligibility", "conclusion": "skipped", "steps": []}, monitor]})
    proof = monitor_recovery.audit_producers("Owner/Repo", gate_time(), gh, full_history=True)
    assert proof["audited_producers"] == (["901"] if status == "completed" else [])


def test_changed_pending_claim_evidence_is_not_inferred_safe():
    def gh(*args):
        if "workflows/" in args[1]:
            return json.dumps({"workflow_runs": [{"id": 901, "status": "pending", "run_attempt": 1,
                "head_branch": "main", "name": "lightweight-live-monitor", "head_repository": {"full_name": "Owner/Repo"}}]})
        return json.dumps({"total_count": 2, "jobs": [
            {"name": "incident-eligibility", "status": "completed", "conclusion": "success",
             "steps": [{"name": "Acquire irreversible incident recovery claim", "conclusion": "success"}]},
            {"name": "monitor", "status": "queued", "steps": []}]})
    with pytest.raises(RuntimeError, match="pending producer"):
        monitor_recovery.audit_producers("Owner/Repo", gate_time(), gh, full_history=True)


@pytest.mark.parametrize("change", [{"enrichment_ack_at": "done"}, {"material": 0},
    {"enrichment_delivery_json": "{}"}, {"analysis_status": "pending"}])
def test_pending_event_changes_fail_closed(gate, monkeypatch, change):
    def trace(path):
        event_id = path.split("=", 1)[1]
        ticker, source = gate.PENDING[event_id]
        return {"event_id": event_id, "ticker": ticker, "source": source,
                "analysis_status": "complete", "material": 1, **change}
    monkeypatch.setattr(gate, "verification_edge_request", trace)
    with pytest.raises(RuntimeError, match="pending Edge"):
        gate.pending_gate()


def test_workflow_lock_covers_gate_and_processing_without_downstream_dispatch():
    text = (Path(__file__).parents[1] / ".github/workflows/lightweight-monitor.yml").read_text()
    assert "group: mozes-hot-monitor" in text
    assert "needs: incident_eligibility" in text
    assert "MOZES_INCIDENT_RECOVERY_ENABLED == '20261011'" in text
    gate_block = text.split("  incident_eligibility:", 1)[1].split("  monitor:", 1)[0]
    assert "contents: write" in gate_block
    assert "gh workflow run" not in gate_block
    assert text.index("python scripts/incident_recovery.py process") < text.index("python -m mozes.edge_sync")
    assert "MAX_REWIND = timedelta(minutes=45)" in (Path(__file__).parents[1] / "scripts/restore_monitor_state.py").read_text()


def test_explicit_manual_request_cannot_fall_through_disabled_eligibility():
    text = (Path(__file__).parents[1] / ".github/workflows/lightweight-monitor.yml").read_text()
    monitor = text.split("  monitor:", 1)[1].split("    runs-on:", 1)[0]
    assert "needs.incident_eligibility.result == 'skipped' &&" in monitor
    assert "inputs.incident_recovery_authorization == ''" in monitor
    assert text.count("group: mozes-hot-monitor") == 1
    assert text.count("Acquire irreversible incident recovery claim") == 1
    assert "MOZES_INCIDENT_MANUAL_APPROVAL: ${{ vars.MOZES_INCIDENT_MANUAL_APPROVAL }}" in text


def analytics(gate, stamp="2026-10-11T01:00:00Z", writes=1):
    return {"data": {"viewer": {"accounts": [{"d1AnalyticsAdaptiveGroups": [
        {"dimensions": {"databaseId": gate.DATABASE, "datetimeHour": stamp},
         "sum": {"rowsWritten": writes}}]}]}}}


@pytest.mark.parametrize("stamp", ["2026-10-11T01:00:00Z", "2026-10-11T01:00:00.000+00:00",
                                     "2026-10-11T04:00:00+03:00"])
def test_analytics_normalizes_equivalent_provider_timestamps(gate, stamp):
    proof = gate.analytics_evidence(analytics(gate, stamp), gate.START + timedelta(hours=1), canonical_events())
    assert proof["capacity_demonstrated"] and proof["current_hour_rows_written"] == 1


@pytest.mark.parametrize("stamp", [None, "2026-10-11T01:00:00", "2026-10-11T00:00:00Z",
                                     "2026-10-11T02:00:00Z", "2026-10-11T01:00:00.001Z"])
def test_stale_missing_or_invalid_analytics_timestamps_fail_closed(gate, stamp):
    with pytest.raises(gate.IncidentGateError):
        gate.analytics_evidence(analytics(gate, stamp), gate.START + timedelta(hours=1), canonical_events())


@pytest.mark.parametrize("writes", [None, "1", True, -1, 1.5, float("inf"), 90000])
def test_invalid_or_exhausted_provider_counters_fail_closed(gate, writes):
    with pytest.raises(gate.IncidentGateError):
        gate.analytics_evidence(analytics(gate, writes=writes), gate.START + timedelta(hours=1), canonical_events())


@pytest.mark.parametrize("kind", ["missing", "empty", "null", "errors", "duplicate", "truncated"])
def test_unavailable_or_incomplete_provider_evidence_fails_closed(gate, kind):
    value = analytics(gate)
    groups = value["data"]["viewer"]["accounts"][0]["d1AnalyticsAdaptiveGroups"]
    if kind == "missing":
        value = {}
    elif kind == "empty":
        groups.clear()
    elif kind == "null":
        value["data"] = None
    elif kind == "errors":
        value["errors"] = [{"message": "unavailable"}]
    elif kind == "duplicate":
        groups.append(copy.deepcopy(groups[0]))
    else:
        groups.extend(copy.deepcopy(groups[0]) for _ in range(99))
    with pytest.raises(gate.IncidentGateError):
        gate.analytics_evidence(value, gate.START + timedelta(hours=1), canonical_events())


def test_hourly_writes_without_recent_legitimate_write_do_not_prove_capacity(gate):
    events = canonical_events()
    for event in events:
        event["enrichment_dispatch_at"] = "2026-10-11T00:01:00Z"
    assert not gate.analytics_evidence(analytics(gate), gate.START + timedelta(hours=1), events)["capacity_demonstrated"]


def test_fingerprints_ignore_order_json_whitespace_and_equivalent_dates(gate):
    events = canonical_events()
    expected = gate.edge_fingerprints(events)
    events.reverse()
    for event in events:
        event["first_seen_at"] = "2026-10-10T15:00:00+03:00"
        if event["enrichment_ack_at"]:
            event["enrichment_alert_ids_json"] = '[ "alert" ]'
    assert gate.edge_fingerprints(events) == expected


@pytest.mark.parametrize("kind", ["duplicate", "missing", "partial_ack", "missing_change", "invalid_receipt"])
def test_incomplete_edge_history_fails_closed(gate, kind):
    events = canonical_events()
    if kind == "duplicate":
        events[1]["event_id"] = events[0]["event_id"]
    elif kind == "missing":
        del events[0]["source_url"]
    elif kind == "partial_ack":
        events[8]["enrichment_change_id"] = events[0]["enrichment_change_id"]
    elif kind == "missing_change":
        events[0]["enrichment_change_id"] = None
    else:
        events[0]["enrichment_delivery_json"] = '{}'
    with pytest.raises(gate.IncidentGateError):
        gate.edge_fingerprints(events)


@pytest.mark.parametrize("sql", ["DELETE FROM edge_events", "SELECT 1", "UPDATE edge_events SET material=0"])
def test_provider_transport_rejects_nonreviewed_sql_before_network(gate, monkeypatch, sql):
    monkeypatch.setenv("CLOUDFLARE_RECOVERY_READ_TOKEN", "test-only-placeholder")
    with pytest.raises(gate.IncidentGateError, match="non-read-only"):
        gate.cf(f"accounts/{gate.ACCOUNT}/d1/database/{gate.DATABASE}/query", body={"sql": sql})


@pytest.mark.parametrize("kind", ["mutation", "fields", "account", "date", "extra_key"])
def test_graphql_transport_rejects_unapproved_requests_before_network(gate, monkeypatch, kind):
    monkeypatch.setenv("CLOUDFLARE_RECOVERY_READ_TOKEN", "test-only-placeholder")
    query = gate.analytics_query(gate.START)
    body = {"query": query}
    if kind == "mutation":
        body["query"] = "mutation { deleteDatabase(id: \"other\") }"
    elif kind == "fields":
        body["query"] = query.replace("rowsWritten", "rowsRead")
    elif kind == "account":
        body["query"] = query.replace(gate.ACCOUNT, "unexpected-account")
    elif kind == "date":
        body["query"] = gate.analytics_query(gate.START - timedelta(days=1))
    else:
        body["variables"] = {}
    def forbidden_network(*args, **kwargs):
        pytest.fail("unapproved GraphQL must be rejected before network construction")
    monkeypatch.setattr(gate.urllib.request, "Request", forbidden_network)
    monkeypatch.setattr(gate.urllib.request, "build_opener", forbidden_network)
    with pytest.raises(gate.IncidentGateError, match="non-read-only"):
        gate.cf("graphql", body=body)


@pytest.mark.parametrize("kind", ["auth", "missing_date", "stale_date", "missing_result", "graphql_null"])
def test_provider_transport_unavailability_is_safe_and_closed(gate, monkeypatch, kind):
    from datetime import datetime, timezone
    from email.utils import format_datetime
    monkeypatch.setenv("CLOUDFLARE_RECOVERY_READ_TOKEN", "test-only-placeholder")
    class Response:
        headers = {"Date": format_datetime(datetime.now(timezone.utc))}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            return json.dumps({"data": None} if kind == "graphql_null" else {"success": True}).encode()
    response = Response()
    if kind == "missing_date":
        response.headers = {}
    elif kind == "stale_date":
        response.headers = {"Date": "Sat, 10 Oct 2026 00:00:00 GMT"}
    class Opener:
        def open(self, *args, **kwargs):
            if kind == "auth":
                raise gate.urllib.error.HTTPError("redacted", 403, "forbidden", {}, None)
            return response
    monkeypatch.setattr(gate.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(gate.IncidentGateError) as error:
        if kind == "graphql_null":
            gate.cf("graphql", body={"query": gate.analytics_query(gate.START)})
        else:
            gate.cf(f"accounts/{gate.ACCOUNT}/d1/database/{gate.DATABASE}")
    assert "test-only-placeholder" not in str(error.value)


def test_credentials_are_step_scoped_and_absent_during_dependency_installation():
    import re
    root = Path(__file__).parents[1]
    for name in ("lightweight-monitor.yml", "verify-monitor-recovery.yml"):
        text = (root / ".github/workflows" / name).read_text()
        for job in re.split(r"^  [a-z_]+:\s*$", text, flags=re.MULTILINE)[1:]:
            if "    steps:" not in job:
                continue
            config, steps = job.split("    steps:", 1)
            for credential in ("GH_TOKEN:", "EDGE_SYNC_TOKEN:", "CLOUDFLARE_RECOVERY_READ_TOKEN:", "MOZES_ALERT_FEED_TOKEN:"):
                assert credential not in config
            for step in re.split(r"^      - ", steps, flags=re.MULTILINE):
                if "pip install" in step or "actions/setup-python" in step or "actions/checkout" in step:
                    assert "CLOUDFLARE_RECOVERY_READ_TOKEN:" not in step
                    assert "EDGE_SYNC_TOKEN:" not in step
                    assert "GH_TOKEN:" not in step
            assert "persist-credentials: false" in steps


def retry_events(gate, queued="2026-10-11T00:55:00Z"):
    events = canonical_events()
    for event in events:
        event["enrichment_dispatch_at"] = "2026-10-10T09:59:01.788Z"
    for event, (event_id, (ticker, source)) in zip(events[-5:], gate.PENDING.items()):
        event.update(event_id=event_id, ticker=ticker, source=source)
    events[-1]["enrichment_queued_at"] = queued
    return events


def test_post_reset_retry_with_old_initial_dispatch_proves_capacity(gate):
    now = gate.START + timedelta(hours=1)
    rows = retry_events(gate)
    expected = gate.edge_fingerprints(rows)
    proof = gate.analytics_evidence(analytics(gate, writes=58), now, rows)
    assert proof["capacity_demonstrated"]
    assert proof["latest_automatic_write_at"] == "2026-10-11T00:55:00+00:00"
    rows[-1]["enrichment_queued_at"] = "2026-10-11T00:56:00Z"
    assert gate.edge_fingerprints(rows) == expected
    assert not rows[-1]["enrichment_ack_at"] and not rows[-1]["enrichment_change_id"]


@pytest.mark.parametrize("queued,writes,expected", [
    (None, 58, False),  # rejected dispatch does not persist a successful queue stamp
    ("2026-10-11T00:55:00Z", 0, False),
    ("2026-10-11T00:50:00Z", 58, True),
    ("2026-10-11T00:49:59.999Z", 58, False),
    ("2026-10-10T23:59:59Z", 58, False),
    ("2026-10-11T00:55:00+00:00", 58, True),
])
def test_retry_requires_fresh_post_reset_stamp_and_independent_writes(gate, queued, writes, expected):
    proof = gate.analytics_evidence(analytics(gate, writes=writes),
        gate.START + timedelta(hours=1), retry_events(gate, queued))
    assert proof["capacity_demonstrated"] is expected


@pytest.mark.parametrize("queued", ["", "invalid", "2026-10-11T00:55:00", 42,
    "2026-10-11T01:00:00.001Z"])
def test_invalid_or_future_queue_evidence_fails_closed(gate, queued):
    with pytest.raises(gate.IncidentGateError):
        gate.analytics_evidence(analytics(gate), gate.START + timedelta(hours=1), retry_events(gate, queued))


@pytest.mark.parametrize("change", [{"ticker": "OTHER"}, {"source": "other"},
    {"event_id": "EDGE-ffffffffffffffffffffffff"}, {"enrichment_ack_at": "done"},
    {"enrichment_change_id": "CHG-ffffffffffffffffffffffff"},
    {"enrichment_dispatch_at": "2026-10-11T00:56:00Z"}])
def test_recent_retry_requires_protected_pending_unacknowledged_identity(gate, change):
    events = retry_events(gate)
    events[-1].update(change)
    with pytest.raises(gate.IncidentGateError):
        gate.analytics_evidence(analytics(gate), gate.START + timedelta(hours=1), events)


def test_missing_queue_projection_or_headroom_cannot_prove_capacity(gate):
    rows = retry_events(gate)
    del rows[-1]["enrichment_queued_at"]
    with pytest.raises(gate.IncidentGateError):
        gate.analytics_evidence(analytics(gate), gate.START + timedelta(hours=1), rows)
    with pytest.raises(gate.IncidentGateError):
        gate.analytics_evidence(analytics(gate, writes=90000), gate.START + timedelta(hours=1), retry_events(gate))


def test_15_minute_schedule_and_16_minute_retry_keep_10_minute_window(gate):
    outcomes = []
    for minute in range(7, 120, 15):
        now = gate.START + timedelta(minutes=minute)
        queued = gate.START + timedelta(minutes=(minute // 16) * 16)
        rows = retry_events(gate, queued.isoformat())
        outcomes.append(gate.analytics_evidence(analytics(gate,
            stamp=now.replace(minute=0).isoformat()), now, rows)["capacity_demonstrated"])
    assert outcomes == [True, True, True, True, True, True, True, True]
    now = gate.START + timedelta(minutes=127)
    queued = gate.START + timedelta(minutes=112)
    assert not gate.analytics_evidence(analytics(gate, stamp="2026-10-11T02:00:00Z"),
        now, retry_events(gate, queued.isoformat()))["capacity_demonstrated"]
    now += timedelta(minutes=15)
    queued = gate.START + timedelta(minutes=128)
    assert not gate.analytics_evidence(analytics(gate, stamp="2026-10-11T02:00:00Z"),
        now, retry_events(gate, queued.isoformat()))["capacity_demonstrated"]


def test_complete_provider_gate_accepts_only_validated_retry_evidence(gate, monkeypatch):
    test_provider_capacity_requires_actual_current_hour_writes(gate, monkeypatch, 58, 625, True)
    original = gate.cf
    rows = retry_events(gate)
    monkeypatch.setattr(gate, "EDGE_FINGERPRINTS", gate.edge_fingerprints(rows))
    def provider(path, **kwargs):
        result = original(path, **kwargs)
        if path.endswith("query"):
            result[1]["results"] = rows
        return result
    monkeypatch.setattr(gate, "cf", provider)
    proof = gate.provider_gate(gate.START + timedelta(hours=1))
    assert proof["capacity_demonstrated"] and proof["daily_rows_written"] == 625
    assert proof["edge_fingerprints"] == gate.EDGE_FINGERPRINTS
    del rows[-1]["enrichment_queued_at"]
    with pytest.raises(gate.IncidentGateError):
        gate.provider_gate(gate.START + timedelta(hours=1))


def test_cloudflare_403_7403_does_not_retry_or_authorize_capacity(gate, monkeypatch):
    import io
    monkeypatch.setenv("CLOUDFLARE_RECOVERY_READ_TOKEN", "test-only-placeholder")
    calls = []
    class Opener:
        def open(self, request, **kwargs):
            calls.append(request.full_url)
            raise gate.urllib.error.HTTPError(request.full_url, 403, "Forbidden", {},
                io.BytesIO(b'{"success":false,"errors":[{"code":7403}]}'))
    monkeypatch.setattr(gate.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(gate.IncidentGateError, match="evidence unavailable; no retry"):
        gate.provider_gate(gate.START + timedelta(hours=1))
    assert len(calls) == 1
