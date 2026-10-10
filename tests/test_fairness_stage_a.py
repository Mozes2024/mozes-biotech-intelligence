"""Prove the code-only rollout patch works without migration 0005."""
import io
import subprocess
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
STAGE_A = "19b75393b1dc3fe504a26fb7d770e2457e86cabf"


def test_history_preserving_stage_a_fairness_patch_and_javascript_suite():
    archive = subprocess.run(["git", "archive", STAGE_A], cwd=ROOT,
                             check=True, capture_output=True).stdout
    with TemporaryDirectory(prefix="mozes-fairness-stage-a-") as scratch:
        worker = Path(scratch)
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(worker, filter="data")
        for patch in ("rollback-stage-a.patch", "queue-fairness-stage-a.patch"):
            path = ROOT / "docs" / "d1" / patch
            subprocess.run(["git", "apply", "--check", str(path)], cwd=worker, check=True)
            subprocess.run(["git", "apply", str(path)], cwd=worker, check=True)
        for name in ("test_fairness.mjs", "test-db.mjs"):
            content = (ROOT / "cloudflare" / name).read_text(encoding="utf-8")
            if name == "test-db.mjs":
                content = content.replace(",'0005_d1_optimization.sql'", "")
            (worker / "cloudflare" / name).write_text(content, encoding="utf-8")
        for suite in ("test.mjs", "test_alert_feed.mjs", "test_hot_edge.mjs", "test_fairness.mjs"):
            result = subprocess.run(["node", "cloudflare/" + suite], cwd=worker,
                                    capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
