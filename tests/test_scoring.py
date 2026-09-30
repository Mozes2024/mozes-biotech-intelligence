from mozes.scoring import catalyst_impact, classify, clinical_evidence
from mozes.versions import VERSIONS


def S(**kw):
    base = {"type": "P3_TOPLINE", "dependency": "single", "mcap": "small", "commercial": False,
            "enables_filing": True, "features": {}}
    base.update(kw)
    return base


def test_impact_is_deterministic():
    assert catalyst_impact(S()) == catalyst_impact(S())


def test_small_single_asset_p3_outranks_megacap_supplement():
    big = catalyst_impact(S(type="PDUFA_SUPP", dependency="one_of_many", mcap="mega", commercial=True, enables_filing=False))
    assert catalyst_impact(S())["score"] > big["score"]


def test_impact_capped_at_100():
    assert catalyst_impact(S(runway_months=6))["score"] == 100


def test_integrity_penalty():
    f = {"prior": "strong", "design": "rct_placebo"}
    a = clinical_evidence(S(features=f))["score"]
    b = clinical_evidence(S(features={**f, "integrity": True}))["score"]
    assert a - b == 15


def test_evidence_is_labeled_as_model_estimate():
    ev = clinical_evidence(S(features={"prior": "moderate"}))
    assert ev["category"] in {"Weak", "Moderate", "Strong", "Very Strong"}
    assert "model estimate" in ev["model_p"]["label"]
    assert ev["model_p"]["lo"] < ev["model_p"]["p"] < ev["model_p"]["hi"]


def test_version_stamps():
    assert catalyst_impact(S())["version"] == VERSIONS["catalyst_impact"]
    assert clinical_evidence(S())["version"] == VERSIONS["clinical_evidence"]


GOOD_SCEN = {"available": True, "bear_mid": -0.3, "asymmetry": 3.0}


def test_critical_flag_forces_avoid():
    r = classify({"score": 90}, {"score": 90}, [{"sev": "critical", "text": "x"}], GOOD_SCEN, 100, True, 30)
    assert r["cls"] == "AVOID"


def test_weak_evidence_forces_avoid():
    assert classify({"score": 90}, {"score": 40}, [], GOOD_SCEN, 100, True, 30)["cls"] == "AVOID"


def test_no_market_data_never_hold():
    assert classify({"score": 90}, {"score": 90}, [], GOOD_SCEN, 100, False, 30)["cls"] == "WATCH"


def test_hold_requires_all_gates():
    assert classify({"score": 90}, {"score": 90}, [], GOOD_SCEN, 100, True, 30)["cls"] == "HOLD"
    assert classify({"score": 90}, {"score": 90}, [{"sev": "high", "text": "x"}], GOOD_SCEN, 100, True, 30)["cls"] == "WATCH"


def test_runup_disabled_until_validated():
    r = classify({"score": 100}, {"score": 70}, [], None, 100, False, 30)
    assert r["cls"] == "WATCH"
