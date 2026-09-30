import json
from mozes.promotion import candidate_matches_statement


def test_exact_nct_is_high_confidence_match():
    c = {"nct_id":"NCT12345678","title":"Trial of Drug X","raw_json":"{}"}
    ok, conf, why = candidate_matches_statement(c, "Topline results from NCT12345678 are expected in December 2026.")
    assert ok and conf == 1.0


def test_unrelated_statement_does_not_promote():
    c = {"nct_id":"NCT12345678","title":"Trial of Drug X","raw_json":"{}"}
    ok, conf, why = candidate_matches_statement(c, "We expect revenue guidance in December 2026.")
    assert not ok
