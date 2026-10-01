"""v2D regression contracts: one active path, reporting-only validation, safe local API."""
import ast
import io
import threading
import time
from pathlib import Path

from mozes import app_server, db
from mozes.radar import bootstrap_database, validation_status
from mozes.validation_evaluator import evaluate_walk_forward


ROOT = Path(__file__).resolve().parents[1]


def test_active_engine_uses_shared_components_not_legacy_scoring():
    tree = ast.parse((ROOT / "mozes" / "engine_v2.py").read_text(encoding="utf-8"))
    imports = [node.module for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert "scoring_common" in imports
    assert "scoring" not in imports


def test_validation_evaluator_reports_without_auto_enabling(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    before = validation_status(conn)
    report = evaluate_walk_forward(conn)
    after = validation_status(conn)
    assert report["gates_auto_enabled"] is False
    assert after["runup"]["enabled"] is before["runup"]["enabled"] is False
    assert after["hold_through"]["enabled"] is before["hold_through"]["enabled"] is False
    assert db.validation_row(conn, "runup")["metrics_json"] != "{}"


def test_static_file_boundary_rejects_parent_traversal():
    assert app_server._web_file("/../README.md") is None
    assert app_server._web_file("/index.html") == app_server.WEB_DIR / "index.html"


def test_post_body_limit_is_rejected_before_reading():
    handler = object.__new__(app_server.Handler)
    handler.headers = {"Content-Length": str(app_server.MAX_POST_BYTES + 1)}
    handler.rfile = io.BytesIO(b"{")
    assert handler._body_json() == "too_large"


def test_background_refresh_returns_run_id_and_sanitizes_failure(tmp_path, monkeypatch):
    done = threading.Event()
    def fail_refresh(*args, **kwargs):
        done.set()
        raise RuntimeError("secret transport failure")
    monkeypatch.setattr(app_server, "refresh_live", fail_refresh)
    jobs = app_server.RefreshJobs(tmp_path / "x.db")
    started = jobs.start({"months": 6})
    assert started["run_id"] and started["status"] in {"QUEUED", "RUNNING"}
    assert done.wait(1)
    for _ in range(100):
        finished = jobs.status(started["run_id"])
        if finished["status"] == "FAILED":
            break
        time.sleep(.01)
    assert finished == {"run_id": started["run_id"], "status": "FAILED", "error": "refresh failed; inspect persisted refresh status"}
