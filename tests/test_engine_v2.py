from mozes.engine_v2 import evidence_for


def test_readout_and_pdufa_use_different_engines():
    readout = evidence_for({"type":"P3_TOPLINE","ta":"oncology","features":{}})
    reg = evidence_for({"type":"PDUFA_NME","features":{}})
    assert readout["engine"] == "clinical_readout_v2"
    assert reg["engine"] == "regulatory_v2"
    assert readout["context_prior"] != reg["context_prior"]


def test_negative_adcom_hits_regulatory_score_hard():
    clean = evidence_for({"type":"PDUFA_NME","features":{}})
    bad = evidence_for({"type":"PDUFA_NME","features":{"negative_adcom":True}})
    assert clean["score"] - bad["score"] >= 30
