from mozes.session import session_from_sec_acceptance


def test_sec_acceptance_sessions():
    assert session_from_sec_acceptance("20260930081500") == "premarket"
    assert session_from_sec_acceptance("20260930120000") == "intraday"
    assert session_from_sec_acceptance("20260930160500") == "afterhours"
    assert session_from_sec_acceptance(None) == "unknown"
