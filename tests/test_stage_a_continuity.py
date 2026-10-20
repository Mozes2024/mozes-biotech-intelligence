import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_stage_a_liveness_keeps_production_schema_fence_history_and_polling(tmp_path):
    sys.path.insert(0, str(ROOT / 'scripts'))
    try:
        spec = importlib.util.spec_from_file_location('stage_a_continuity', ROOT / 'scripts/build_stage_a_continuity.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        baseline = builder.stabilized(tmp_path / 'baseline')
        candidate = builder.build(tmp_path / 'candidate')
    finally:
        sys.path.pop(0)
    changed = {str(p.relative_to(candidate)).replace('\\', '/') for p in candidate.rglob('*')
               if p.is_file() and (not (baseline / p.relative_to(candidate)).exists()
                                   or p.read_bytes() != (baseline / p.relative_to(candidate)).read_bytes())}
    assert changed == {'cloudflare/src/hot-edge.js', 'cloudflare/src/index.js', 'cloudflare/src/monitor-heartbeat.js'}
    helper = (ROOT / 'cloudflare/test-db.mjs').read_text().replace(",'0005_d1_optimization.sql'", '')
    (candidate / 'cloudflare/test-db.mjs').write_text(helper, encoding='utf-8')
    for name in ['test_stage_a_stabilization.mjs', 'test_stage_a_continuity.mjs']:
        (candidate / 'cloudflare' / name).write_bytes((ROOT / 'cloudflare' / name).read_bytes())
        result = subprocess.run(['node', 'cloudflare/' + name], cwd=candidate, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
