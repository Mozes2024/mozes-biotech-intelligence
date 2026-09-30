from mozes.engine_v2 import recommendation_status


def test_experimental_investment_candidate_is_transparent_and_does_not_open_gates():
    result = recommendation_status(
        {"verification_state": "VERIFIED"}, {"score": 80}, {"score": 80}, [], {"available": True},
        {"class": "REVIEW"}, {"runup": {"satisfied": False}, "hold_through": {"satisfied": False}},
    )
    assert result["status"] == "INVESTMENT_CANDIDATE"
    assert result["experimental"] and result["validation_gates_separate"]
    assert "אימות אמפירי" in result["missing_he"]


def test_unverified_event_is_missing_information():
    result = recommendation_status(
        {"verification_state": "QUARANTINED"}, {"score": 90}, {"score": 90}, [], {"available": True},
        {"class": "QUARANTINED"}, {},
    )
    assert result["status"] == "INSUFFICIENT_INFORMATION"
