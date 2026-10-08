"""Keep legacy scoring available to frozen regression only."""
import ast
from pathlib import Path

from mozes.scoring_common import catalyst_impact, risk_flags


ROOT = Path(__file__).resolve().parents[1] / "mozes"
LEGACY_COMPATIBILITY = {"analysis.py", "scoring.py"}


def test_active_product_modules_do_not_import_legacy_scoring():
    offenders = []
    for path in ROOT.glob("*.py"):
        if path.name in LEGACY_COMPATIBILITY:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "") in {"scoring", "mozes.scoring"}:
                offenders.append(path.name)
            if isinstance(node, ast.Import) and any(alias.name == "mozes.scoring" for alias in node.names):
                offenders.append(path.name)
    assert offenders == []


def test_short_runway_is_risk_only():
    event = {"type": "P2_TOPLINE", "dependency": "lead", "mcap": "small"}
    long = catalyst_impact({**event, "runway_months": 18})
    short = catalyst_impact({**event, "runway_months": 6})
    assert short["score"] == long["score"]
    assert all(item["code"] != "short_cash_runway" for item in short["contributions"])
    assert "short_cash_runway" in {item["code"] for item in risk_flags({**event, "runway_months": 6})}
