from mozes.source_quality import verification_state


def test_primary_exact_is_verified():
    v = verification_state([{"source_type": "sec", "published_at": "2026-01-01"}], "exact")
    assert v["state"] == "VERIFIED"
    assert v["confidence"] >= 95


def test_ctgov_is_discovery_only():
    v = verification_state([{"source_type": "clinicaltrials"}], "month")
    assert v["state"] == "DISCOVERED"
    assert v["confidence"] < 90


def test_secondary_calendar_is_quarantined():
    v = verification_state([{"source_type": "secondary_calendar"}], "exact")
    assert v["state"] == "QUARANTINED"
