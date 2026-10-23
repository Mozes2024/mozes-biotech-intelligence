"""Bounded retries for transient GitHub artifact egress failures."""
import importlib.util
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def restore(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    spec = importlib.util.spec_from_file_location(
        'restore_monitor_state_retry',
        Path(__file__).resolve().parents[1] / 'scripts/restore_monitor_state.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_download_retries_transient_failure(restore, monkeypatch, tmp_path):
    calls = []
    def gh(*args, **kwargs):
        calls.append(args)
        if len(calls) < 3:
            (tmp_path / 'partial').write_text('junk')
            raise subprocess.CalledProcessError(1, ['gh'])
        (tmp_path / 'mozes-live.db').write_text('db')
        return ''
    monkeypatch.setattr(restore, 'gh', gh)
    monkeypatch.setattr(restore.time, 'sleep', lambda seconds: None)
    restore.download_monitor_artifact('123', str(tmp_path))
    assert len(calls) == 3
    assert not (tmp_path / 'partial').exists()  # partial download cleared before retry


def test_download_gives_up_after_bounded_attempts(restore, monkeypatch, tmp_path):
    monkeypatch.setattr(restore, 'gh',
                        lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, ['gh'])))
    monkeypatch.setattr(restore.time, 'sleep', lambda seconds: None)
    with pytest.raises(subprocess.CalledProcessError):
        restore.download_monitor_artifact('123', str(tmp_path))
