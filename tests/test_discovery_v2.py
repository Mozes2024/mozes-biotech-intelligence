import json

from mozes.discovery import study_to_candidate


def test_ctgov_candidate_is_never_verified_by_itself():
    study = {"protocolSection": {
        "identificationModule": {"nctId":"NCT12345678","briefTitle":"Drug X Phase 3"},
        "statusModule": {"overallStatus":"RECRUITING","primaryCompletionDateStruct":{"date":"2026-12"},"lastUpdatePostDateStruct":{"date":"2026-09-01"}},
        "designModule": {"phases":["PHASE3"]},
        "sponsorCollaboratorsModule": {"leadSponsor":{"name":"Example Therapeutics Inc."}},
        "armsInterventionsModule": {"interventions":[{"name":"Drug X"}]},
    }}
    c = study_to_candidate(study, [{"sponsor_norm":"example","sponsor":"Example Therapeutics","ticker":"EXM","confidence":0.95}])
    assert c["verification_state"] == "DISCOVERED"
    assert "NOT a readout date" in c["date_semantics"]
    assert c["ticker"] == "EXM"
