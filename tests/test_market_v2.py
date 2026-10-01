from mozes.market import event_return, join_on_dates


def test_join_is_by_calendar_date_not_row_position():
    s = [{"date":"2026-01-02","close":10},{"date":"2026-01-05","close":11},{"date":"2026-01-07","close":12}]
    b = [{"date":"2026-01-02","close":100},{"date":"2026-01-06","close":101},{"date":"2026-01-07","close":102}]
    pairs = join_on_dates(s,b)
    assert [x[0]["date"] for x in pairs] == ["2026-01-02","2026-01-07"]


def test_premarket_and_afterhours_use_different_post_close():
    rows = [
        {"date":"2026-01-02","close":10},
        {"date":"2026-01-05","close":15},
        {"date":"2026-01-06","close":20},
    ]
    pre = event_return(rows,"2026-01-05","premarket")
    ah = event_return(rows,"2026-01-05","afterhours")
    assert pre["to_date"] == "2026-01-05"
    assert ah["to_date"] == "2026-01-06"
    assert pre["return"] == 0.5
    assert abs(ah["return"] - (20 / 15 - 1)) < 1e-12


def test_unknown_session_is_flagged():
    rows = [{"date":"2026-01-02","close":10},{"date":"2026-01-05","close":15},{"date":"2026-01-06","close":20}]
    r = event_return(rows,"2026-01-05","unknown")
    assert r["timing_uncertain"] is True
