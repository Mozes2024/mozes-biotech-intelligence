from pathlib import Path
import subprocess
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stage_a_builder',ROOT/'scripts/build_stage_a_stabilized.py')
builder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
build=builder.build


def test_stage_a_combined_forward_only_queue_and_blocked_analysis_without_migration_0005(tmp_path):
    candidate=build(tmp_path/'candidate')
    helper=(ROOT/'cloudflare/test-db.mjs').read_text(encoding='utf-8').replace(",'0005_d1_optimization.sql'",'')
    (candidate/'cloudflare/test-db.mjs').write_text(helper,encoding='utf-8')
    (candidate/'cloudflare/test_stage_a_stabilization.mjs').write_bytes((ROOT/'cloudflare/test_stage_a_stabilization.mjs').read_bytes())
    result=subprocess.run(['node','cloudflare/test_stage_a_stabilization.mjs'],cwd=candidate,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    for name in ('hot-edge.js','forward-policy.js','candidate-contract.js','edge-contract.js','index.js'):
        subprocess.run(['node','--check','cloudflare/src/'+name],cwd=candidate,check=True)
