from datetime import date

from mozes.dates import normalize_date_text as N
from mozes.dates import pub_effective

REF = date(2026, 1, 15)


def test_quarter_q4():
    w = N("Topline expected Q4 2026", REF)
    assert (w.precision, w.start, w.end) == ("quarter", "2026-10-01", "2026-12-31")


def test_quarter_4q():
    assert N("PEAK topline expected 4Q 2026", REF).start == "2026-10-01"


def test_quarter_words():
    w = N("in the fourth quarter of 2026", REF)
    assert w.precision == "quarter" and w.end == "2026-12-31"


def test_half():
    w = N("second half of 2026", REF)
    assert (w.precision, w.start, w.end) == ("half", "2026-07-01", "2026-12-31")


def test_exact():
    w = N("on December 14, 2026", REF)
    assert (w.precision, w.start, w.confidence) == ("exact", "2026-12-14", 95)


def test_exact_confirmed_by_company():
    assert N("December 14, 2026", REF, confirmed_by_company=True).confidence == 100


def test_abbreviated_month():
    assert N("Dec 23, 2026", REF).start == "2026-12-23"


def test_month():
    w = N("expected in September 2026", REF)
    assert (w.precision, w.start, w.end) == ("month", "2026-09-01", "2026-09-30")


def test_late_year():
    w = N("late 2026", REF)
    assert w.precision == "early_late_year" and w.start == "2026-09-01"


def test_mid_year():
    w = N("mid-2026", REF)
    assert (w.precision, w.start, w.end) == ("mid_year", "2026-05-01", "2026-08-31")


def test_next_year_is_very_low_confidence():
    w = N("next year", date(2026, 9, 30))
    assert (w.precision, w.start, w.confidence) == ("relative", "2027-01-01", 10)


def test_year_only_no_fabricated_precision():
    w = N("expected in 2027", REF)
    assert (w.precision, w.start, w.end) == ("year", "2027-01-01", "2027-12-31")


def test_confidence_hierarchy():
    texts = ["December 14, 2026", "September 2026", "Q4 2026", "second half of 2026", "2027", "next year"]
    vals = [N(t, REF).confidence for t in texts]
    assert vals == sorted(vals, reverse=True) and len(set(vals)) == len(vals)


def test_secondary_source_penalty():
    assert N("December 14, 2026", REF, secondary_source=True).confidence == 80


def test_iso_date():
    assert N("2026-10-20", REF).start == "2026-10-20"


def test_unknown():
    assert N("no date here", REF).precision == "unknown"


def test_pub_effective_is_conservative():
    assert pub_effective("2026-09") == "2026-09-30"
    assert pub_effective("2026") == "2026-12-31"
    assert pub_effective(None) == "9999-12-31"
