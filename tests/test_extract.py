from datetime import date

from mozes.extract import extract_catalyst_statements

TEXT = ("Topline data from Pivotal Analysis 1 are expected in December 2026. Net loss was $61.5 million. "
        "The FDA assigned a PDUFA target action date of November 14, 2026.")


def test_extracts_only_dated_catalyst_statements():
    out = extract_catalyst_statements(TEXT, date(2026, 8, 10), source_id="x", reliability="primary")
    assert len(out) == 2
    assert out[0]["catalyst_type"] == "READOUT" and out[0]["window"]["precision"] == "month"
    assert out[1]["catalyst_type"] == "PDUFA"
    assert out[1]["window"]["start"] == "2026-11-14" and out[1]["window"]["confidence"] == 100
    assert all(o["source_id"] == "x" for o in out)
