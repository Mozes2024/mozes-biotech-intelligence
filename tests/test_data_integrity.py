from mozes.analysis import run_all
from mozes.data_loader import load_historical, load_live, load_outcomes, load_sources


def test_ids_unique():
    ids = [e["id"] for e in load_historical() + load_live()]
    assert len(ids) == len(set(ids))


def test_every_dated_statement_has_a_resolvable_source():
    sources = load_sources()
    for e in load_live() + load_historical():
        for c in e.get("chronology", []):
            if c.get("date_text"):
                assert c.get("src") in sources, (e["id"], c)
                assert sources[c["src"]]["url"].startswith("http")


def test_outcomes_cover_historical_exactly():
    assert set(load_outcomes()) == {h["id"] for h in load_historical()}


def test_sources_have_provenance_fields():
    for s in load_sources().values():
        assert s["url"].startswith("http") and s["retrieved"] and s["reliability"] in ("primary", "secondary")


def test_pipeline_runs_and_stamps_versions():
    r = run_all()
    for a in r["live"] + r["historical"]:
        assert a["versions"]["clinical_evidence"] and a["classification"]["cls"] in {"RUNUP", "HOLD", "WATCH", "AVOID"}
