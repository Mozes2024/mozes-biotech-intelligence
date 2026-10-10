"""Incident scheduling must not turn a stale checkpoint into a reusable bypass."""
import importlib.util
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from mozes import monitor_recovery


@pytest.fixture
def gate(monkeypatch):
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
    monkeypatch.setattr(m.restore, "restore", lambda *a, **k: restores.append(k) or True)
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


def test_claim_race_has_one_winner(gate, monkeypatch):
    state, _ = ready(gate, monkeypatch)
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


def test_crash_after_claim_cannot_be_adopted_by_next_run(gate, monkeypatch):
    _, restores = ready(gate, monkeypatch)
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
    def cf(path, **kwargs):
        if path.endswith("settings"):
            return settings
        if path.endswith("deployments"):
            return {"deployments": [{"created_on": "now", "versions": [{"version_id": gate.WORKER, "percentage": 100}]}]}
        if path.endswith("schedules"):
            return {"schedules": [{"cron": "*/2 * * * *"}]}
        if path.endswith("content/v2"):
            return (b'--BOUND\r\nContent-Disposition: form-data; name="index.js"; filename="index.js"\r\n\r\nbundle\r\n--BOUND--\r\n',
                    'multipart/form-data; boundary="BOUND"')
        if path.endswith("query"):
            assert "SELECT" in kwargs["body"]["sql"] and "INSERT" not in kwargs["body"]["sql"]
            return [{"success": True, "meta": {"rows_written": 0}, "results": [{"name": name} for name in
                ["0001_alert_feed.sql", "0002_hot_edge.sql", "0003_hot_edge_reliability.sql", "0004_clinical_candidates.sql"]]},
                {"success": True, "meta": {"rows_written": 0}, "results": [{"enrichment_ack_at": "done" if n < 8 else None} for n in range(27)]}]
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
