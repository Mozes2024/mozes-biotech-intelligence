"""The npm/CI entrypoint must discover tests and propagate failures."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('fails', [False, True])
def test_runner_discovers_new_files_excludes_helpers_and_propagates_failure(tmp_path, fails):
    worker = tmp_path/'cloudflare'
    worker.mkdir()
    (worker/'run-tests.mjs').write_bytes((ROOT/'cloudflare/run-tests.mjs').read_bytes())
    (worker/'test.mjs').write_text("import assert from 'node:assert/strict'; assert.ok(true);")
    (worker/'test_future.mjs').write_text("throw Error('regression');" if fails else '')
    for excluded in ('test-db.mjs', 'test_stage_a_stabilization.mjs'):
        (worker/excluded).write_text("throw Error('must never execute directly');")
    result = subprocess.run(['node', str(worker/'run-tests.mjs')], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert (result.returncode != 0) is fails, result.stdout+result.stderr
    assert '2 standalone files' in result.stdout
    assert 'must never execute directly' not in result.stdout+result.stderr
