"""Hot-lane restore must not silently rewind to a pre-alert snapshot."""
from __future__ import annotations

import importlib.util
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore_monitor_state.py"


def module():
    spec = importlib.util.spec_from_file_location("restore_monitor", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _db(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()


def test_restore_refuses_old_candidate_when_newer_successes_exist(tmp_path, monkeypatch, capsys):
    script = module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Owner/Repo")
    monkeypatch.setenv("MOZES_DB_PATH", str(tmp_path / "mozes-live.db"))

    def gh(*args, timeout=300):
        if args[:2] == ("run", "list") and "pages.yml" in args:
            return "[]"
        if args[:2] == ("run", "list"):
            return json.dumps([
                {"databaseId": 200, "createdAt": "2026-10-07T13:20:00Z", "conclusion": "success"},
                {"databaseId": 100, "createdAt": "2026-10-07T10:24:00Z", "conclusion": "success"},
            ])
        if args[0] == "api" and args[1].endswith("/200"):
            raise subprocess.CalledProcessError(1, ["gh", *args])
        if args[0] == "api" and args[1].endswith("/100"):
            return json.dumps({
                "head_branch": "main", "name": "lightweight-live-monitor",
                "head_repository": {"full_name": "Owner/Repo"}, "conclusion": "success",
            })
        if args[:3] == ("run", "download", "100"):
            root = Path(args[-1])
            _db(root / "mozes-live.db")
            (root / "manifest.json").write_text(json.dumps({
                "generated_at": "2026-10-07T10:26:00+00:00"}), encoding="utf-8")
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(script, "gh", gh)
    with pytest.raises(RuntimeError, match="refusing empty/old bootstrap"):
        script.restore()
    out = capsys.readouterr().out
    assert "Refusing to rewind" in out or "State restore unavailable for run 200" in out
    assert not (tmp_path / "mozes-live.db").exists()


@pytest.mark.parametrize("conclusion,checkpoint_valid", [("cancelled", True), ("failure", True), ("cancelled", False)])
def test_cancelled_or_failed_uploaded_checkpoint_survives_ack_boundary(tmp_path, monkeypatch, conclusion, checkpoint_valid):
    script = module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Owner/Repo")
    destination = tmp_path / "restored.db"
    monkeypatch.setenv("MOZES_DB_PATH", str(destination))
    def gh(*args, timeout=300):
        if args[:2] == ("run", "list") and "pages.yml" in args:
            return "[]"
        if args[:2] == ("run", "list"):
            return json.dumps([{"databaseId": 200, "createdAt": "2026-10-08T08:00:00Z", "conclusion": conclusion}])
        if args[0] == "api":
            return json.dumps({"head_branch": "main", "name": "lightweight-live-monitor",
                "head_repository": {"full_name": "Owner/Repo"}, "conclusion": conclusion})
        if args[:3] == ("run", "download", "200"):
            root = Path(args[-1]); source = root / "mozes-live.db"; _db(source)
            (root / "edge-checkpoint.json").write_text(json.dumps({"schema": 1, "producer_run_id": "200",
                "db_sha256": hashlib.sha256(source.read_bytes()).hexdigest() if checkpoint_valid else "invalid"}))
            return ""
        raise AssertionError(args)
    monkeypatch.setattr(script, "gh", gh)
    if checkpoint_valid:
        assert script.restore()
        with sqlite3.connect(destination) as conn:
            assert conn.execute("SELECT x FROM t").fetchone()[0] == 1
    else:
        with pytest.raises(RuntimeError, match="refusing empty/old bootstrap"):
            script.restore()
        assert not destination.exists()
