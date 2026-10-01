from mozes.payload_v3 import _apply_public_research_label_guard


def _gates(runup=False, hold=False):
    return {
        "runup": {"satisfied": runup},
        "hold_through": {"satisfied": hold},
    }


def test_investment_candidate_label_is_blocked_while_empirical_gates_locked():
    rows = [{"recommendation": {"status": "INVESTMENT_CANDIDATE", "label_he": "מועמדת להשקעה"}}]
    _apply_public_research_label_guard(rows, _gates())
    assert rows[0]["recommendation"]["status"] == "HIGH_RESEARCH_PRIORITY"
    assert rows[0]["recommendation"]["label_he"] == "עדיפות מחקר גבוהה"


def test_investment_candidate_label_can_survive_after_gate_opens():
    rows = [{"recommendation": {"status": "INVESTMENT_CANDIDATE", "label_he": "מועמדת להשקעה"}}]
    _apply_public_research_label_guard(rows, _gates(runup=True))
    assert rows[0]["recommendation"]["status"] == "INVESTMENT_CANDIDATE"
