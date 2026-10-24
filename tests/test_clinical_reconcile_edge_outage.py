import json
from urllib.error import HTTPError, URLError

from mozes import clinical_reconcile


def _run_process(tmp_path, monkeypatch, raising):
    monkeypatch.setenv("MOZES_EDGE_SYNC_URL", "https://edge.example/edge/sync")
    monkeypatch.setenv("EDGE_SYNC_TOKEN", "secret")
    monkeypatch.setenv("MOZES_DB_PATH", str(tmp_path / "c.db"))
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setattr("sys.argv", ["clinical_reconcile", "process"])
    monkeypatch.setattr(clinical_reconcile, "edge_request", lambda *a, **k: (_ for _ in ()).throw(raising))
    return raising


def test_clinical_process_survives_edge_5xx(tmp_path, monkeypatch, capsys):
    err = HTTPError("https://edge.example/candidates", 503, "unavailable", {}, None)
    _run_process(tmp_path, monkeypatch, err)
    clinical_reconcile.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "EDGE_UNREACHABLE" and out["best_effort"] is True


def test_clinical_process_survives_edge_transport_failure(tmp_path, monkeypatch, capsys):
    _run_process(tmp_path, monkeypatch, URLError("connection refused"))
    clinical_reconcile.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "EDGE_UNREACHABLE" and out["best_effort"] is True


def test_clinical_process_keeps_4xx_fatal(tmp_path, monkeypatch):
    import pytest
    err = HTTPError("https://edge.example/candidates", 403, "forbidden", {}, None)
    _run_process(tmp_path, monkeypatch, err)
    with pytest.raises(HTTPError):
        clinical_reconcile.main()
