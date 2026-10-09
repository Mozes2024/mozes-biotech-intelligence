"""Recovery must prove history continuity before enabling any source processing."""
import hashlib
import io
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mozes import monitor_recovery as recovery
from test_restore_no_rewind import module, _db

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
OLD = "2026-10-08T13:45:32Z"
SHA = "3d69bf52904fff2a235c1c0ee9321672f2989762"


def restore_fixture(tmp_path, monkeypatch, *, missing=False, corrupt=False):
    script = module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Owner/Repo")
    monkeypatch.setenv("MOZES_DB_PATH", str(tmp_path / "state" / "live.db"))
    source = tmp_path / "original.db"
    _db(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    def gh(*args, **kwargs):
        if args[0] == "api" and "actions/artifacts?" in args[1]:
            return json.dumps({"artifacts": [{"name": "mozes-live-monitor", "created_at": OLD,
                "workflow_run": {"id": 100, "head_branch": "main"}, "expired": missing}]})
        if args[0] == "api":
            return json.dumps({"head_branch": "main", "name": "lightweight-live-monitor",
                "head_repository": {"full_name": "Owner/Repo"}, "conclusion": "success"})
        if args[:2] == ("run", "list"):
            return "[]"
        if args[:2] == ("run", "download"):
            if missing:
                raise subprocess.CalledProcessError(1, ["gh"])
            root = Path(args[-1])
            (root / "mozes-live.db").write_bytes(source.read_bytes())
            (root / "manifest.json").write_text(json.dumps({"generated_at": OLD}))
            (root / "edge-checkpoint.json").write_text(json.dumps({"schema": 1,
                "producer_run_id": "100", "created_at": OLD,
                "db_sha256": "wrong" if corrupt else digest}))
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(script, "gh", gh)
    return script, digest


def test_artifact_discovery_ignores_artifactless_failed_attempts(tmp_path, monkeypatch):
    script, _ = restore_fixture(tmp_path, monkeypatch)
    assert script._list_success_runs() == [{"databaseId": 100, "createdAt": OLD}]
    with pytest.raises(RuntimeError):
        script.restore(now=NOW)  # Finding the producer does not remove the age gate.
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("missing,corrupt", [(True, False), (False, True)])
def test_missing_expired_or_corrupt_latest_never_falls_back(tmp_path, monkeypatch, missing, corrupt):
    script, digest = restore_fixture(tmp_path, monkeypatch, missing=missing, corrupt=corrupt)
    with pytest.raises(RuntimeError):
        script.restore("100", recovery_sha256=digest, now=NOW)
    assert not (tmp_path / "state").exists()


def test_verified_recovery_and_retry_preserve_database_bytes(tmp_path, monkeypatch):
    script, digest = restore_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(recovery, "audit_producers", lambda *a, **k: {"audited_producers": ["101"]})
    monkeypatch.setattr(recovery, "verify_durable_history", lambda *a: {"verified_feed_changes": 37})
    assert script.restore("100", recovery_sha256=digest, verify_only=True, now=NOW)
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / ".monitor").exists()
    for _ in range(2):
        assert script.restore("100", recovery_sha256=digest, now=NOW)
        assert hashlib.sha256((tmp_path / "state/live.db").read_bytes()).hexdigest() == digest
    proof = json.loads((tmp_path / "state/recovery-proof.json").read_text())
    assert proof["source_sha256"] == digest
    assert proof["source_manifest_at"].startswith("2026-10-08")  # Never fake source freshness.


def test_recovery_failure_preserves_existing_local_history(tmp_path, monkeypatch):
    script, digest = restore_fixture(tmp_path, monkeypatch)
    destination = tmp_path / "state/live.db"
    destination.parent.mkdir()
    destination.write_bytes(b"newer history")
    def fail(*a, **k):
        raise RuntimeError("newer producer delivered")
    monkeypatch.setattr(recovery, "audit_producers", fail)
    with pytest.raises(RuntimeError):
        script.restore("100", recovery_sha256=digest, now=NOW)
    assert destination.read_bytes() == b"newer history"


def test_explicit_source_cannot_supersede_newer_checkpoint(tmp_path, monkeypatch):
    script, digest = restore_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(script, "_list_success_runs", lambda: [{"databaseId": 200, "createdAt": OLD}])
    with pytest.raises(RuntimeError):
        script.restore("100", recovery_sha256=digest, now=NOW)
    assert not (tmp_path / "state").exists()


def test_empty_artifact_registry_never_bootstraps(tmp_path, monkeypatch):
    script, _ = restore_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(script, "_list_success_runs", lambda: [])
    with pytest.raises(RuntimeError, match="refusing empty/old bootstrap"):
        script.restore(now=NOW)
    assert not (tmp_path / "state").exists()


def producer_gh(*, enrichment="skipped", delivery="skipped", log="", sha=SHA, attempt=1, missing_step=False):
    def gh(*args):
        if "workflows/" in args[1]:
            return json.dumps({"workflow_runs": [{"id": 101, "status": "completed", "run_attempt": attempt,
                "head_branch": "main", "head_sha": sha, "name": "lightweight-live-monitor",
                "head_repository": {"full_name": "Owner/Repo"}}]})
        if "/jobs?" in args[1]:
            steps = [{"name": n, "conclusion": delivery} for n in recovery.DELIVERY_STEPS]
            if missing_step:
                steps.pop()
            steps.append({"name": "Enrich the correlated Edge event", "conclusion": enrichment})
            return json.dumps({"total_count": 1, "jobs": [{"steps": steps}]})
        return log
    return gh


def test_audit_accepts_failed_restore_and_reviewed_prewrite_timeout():
    for gh in (producer_gh(), producer_gh(enrichment="failure", log="in fetch_wire\nTimeoutError: timed out")):
        assert recovery.audit_producers("Owner/Repo", NOW, gh, now=NOW)["audited_producers"] == ["101"]


@pytest.mark.parametrize("kwargs", [
    {"delivery": "failure"}, {"delivery": "success"}, {"missing_step": True}, {"attempt": 2},
    {"enrichment": "success"}, {"enrichment": "failure", "log": "TimeoutError: in record_change"},
    {"enrichment": "failure", "log": "in fetch_wire\nTimeoutError:", "sha": "unreviewed"},
])
def test_audit_rejects_possible_newer_writes_and_incomplete_evidence(kwargs):
    with pytest.raises(RuntimeError):
        recovery.audit_producers("Owner/Repo", NOW, producer_gh(**kwargs), now=NOW)


def history(tmp_path):
    path = tmp_path / "history.db"
    with sqlite3.connect(path) as c:
        c.executescript("CREATE TABLE change_events(change_id TEXT); INSERT INTO change_events VALUES('CHG-old');"
            "CREATE TABLE alert_outbox(change_id TEXT,status TEXT); INSERT INTO alert_outbox VALUES('CHG-old','sent');"
            "CREATE TABLE edge_event_links(edge_event_id TEXT,change_id TEXT);"
            "INSERT INTO edge_event_links VALUES('EDGE-old','CHG-old');")
    return path


def test_history_verification_keeps_sent_receipts_and_ack_ids(tmp_path, monkeypatch):
    path = history(tmp_path)
    monkeypatch.setenv("MOZES_EDGE_SYNC_URL", "https://trusted.example/edge/universe")
    trace = lambda _: {"enrichment_ack_at": "now", "enrichment_change_id": "CHG-old"}
    feed = lambda *a, **k: io.BytesIO(json.dumps({"schema": 1, "alerts": [{"change_id": "CHG-old", "delivered": True}]}).encode())
    before = path.read_bytes()
    for _ in range(2):
        assert recovery.verify_durable_history(path, trace=trace, opener=feed)["verified_edge_completions"] == 1
    assert path.read_bytes() == before


@pytest.mark.parametrize("case", ["new_change", "missing_sent", "nonterminal", "ack_mismatch"])
def test_history_gate_blocks_loss_or_duplicate_delivery(tmp_path, monkeypatch, case):
    path = history(tmp_path)
    monkeypatch.setenv("MOZES_EDGE_SYNC_URL", "https://trusted.example")
    if case in ("missing_sent", "nonterminal"):
        with sqlite3.connect(path) as c:
            c.execute("UPDATE alert_outbox SET status=?", ("dead" if case == "missing_sent" else "sending",))
    trace = lambda _: {"enrichment_ack_at": "now", "enrichment_change_id": "CHG-new" if case == "ack_mismatch" else "CHG-old"}
    feed = lambda *a, **k: io.BytesIO(json.dumps({"schema": 1, "alerts": [{"change_id": "CHG-new" if case == "new_change" else "CHG-old", "delivered": True}]}).encode())
    with pytest.raises(RuntimeError):
        recovery.verify_durable_history(path, trace=trace, opener=feed)
