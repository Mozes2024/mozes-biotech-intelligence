import json

from datetime import date
from urllib.parse import parse_qs, urlparse

from mozes.discovery import discover, query_url, store_candidates, study_to_candidate
from mozes.pipeline_v2c import aggregate_status


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


def test_explicit_bounded_errors_remain_amber():
    details = {"prices": {"status": "INCOMPLETE", "errors": ["one ticker"]},
               "financials": {"status": "INCOMPLETE"},
               "financing": {"status": "INCOMPLETE"}}
    assert aggregate_status(details) == "INCOMPLETE"
    assert aggregate_status({**details, "refresh": {"status": "PARTIAL"}}) == "PARTIAL"
    assert aggregate_status({"provider": {"status": "FAILED"}}) == "PARTIAL"
    assert aggregate_status({"legacy": {"errors": ["provider down"]}}) == "PARTIAL"


def test_ctgov_query_has_broad_bounded_discovery_fields():
    params = parse_qs(urlparse(query_url("2026-01-01", "2027-01-01")).query)
    advanced = params["filter.advanced"][0]
    assert "PHASE4" in advanced
    assert "CollaboratorClass]INDUSTRY" in advanced
    assert params["pageSize"] == ["1000"]
    assert params["sort"] == ["LastUpdatePostDate:desc"]
    assert {"CollaboratorName", "CollaboratorClass"} <= set(params["fields"][0].split(","))


def test_academic_lead_collaborator_mapping_and_ambiguity():
    study = {"protocolSection": {"identificationModule": {"nctId": "NCT00000001"},
             "designModule": {"phases": ["PHASE4"]},
             "sponsorCollaboratorsModule": {"leadSponsor": {"name": "University Hospital", "class": "OTHER"},
                                            "collaborators": [{"name": "ModernaTX, Inc.", "class": "INDUSTRY"}]},
             "armsInterventionsModule": {"interventions": [{"name": "mRNA-xxxx"}]}}}
    maps = [{"sponsor_norm": "modernatx", "sponsor": "ModernaTX, Inc.", "ticker": "MRNA",
             "cik": "1", "confidence": .9},
            {"sponsor_norm": "another", "sponsor": "Another, Inc.", "ticker": "OTHER",
             "cik": "2", "confidence": .9}]
    candidate = study_to_candidate(study, maps)
    assert (candidate["ticker"], candidate["mapping_basis"], candidate["phase"]) == ("MRNA", "collaborator", "PHASE4")
    assert candidate["collaborators"][0]["class"] == "INDUSTRY"
    study["protocolSection"]["sponsorCollaboratorsModule"]["collaborators"].append(
        {"name": "Another, Inc.", "class": "INDUSTRY"})
    candidate = study_to_candidate(study, maps)
    assert candidate["ticker"] is None
    assert candidate["mapping_basis"] == "ambiguous_collaborators"
    assert len(candidate["mapping_diagnostics"]["matched_issuers"]) == 2


def test_cursor_owns_scan_across_date_boundary():
    urls = []
    def fetcher(url):
        urls.append(url)
        return {"studies": [], "totalCount": 1001,
                "nextPageToken": "second" if "pageToken=" not in url else None}
    _, first = discover(today=date(2026, 10, 1), lookback_days=0, fetcher=fetcher,
                        max_pages=1, return_metadata=True)
    _, second = discover(today=date(2026, 10, 2), lookback_days=0, fetcher=fetcher,
                         max_pages=1, resume=first["resume_cursor"], return_metadata=True)
    assert first["resume_cursor"]["anchor_date"] == "2026-10-01"
    assert "2026-10-01" in urls[1] and "pageToken=second" in urls[1]
    assert second["complete"] and second["resume_cursor"] is None


def test_subsidiary_alias_requires_exact_sec_exhibit(monkeypatch):
    from mozes import refresh
    from mozes.ingest import edgar
    parent = {"sponsor": "Example, Inc.", "sponsor_norm": "example", "ticker": "EXM",
              "cik": "123", "confidence": .9, "source": "SEC-v2C-equity"}
    candidate = {"sponsor": "ExampleTX, Inc.", "ticker": None, "collaborators": []}
    monkeypatch.setattr(refresh, "recent_filings_v2", lambda *a, **kw: [
        {"index_url": "https://www.sec.gov/index", "url": "https://www.sec.gov/filing"}])
    def get(url, **kw):
        return ('{"directory":{"item":[{"name":"exhibit21.htm"}]}}'
                if url.endswith('/index') else '<tr><td>ExampleTX, Inc.</td><td>Delaware</td></tr>')
    monkeypatch.setattr(edgar, "_get", get)
    assert refresh._verified_subsidiary_maps([candidate], [parent])[0]["ticker"] == "EXM"
    monkeypatch.setattr(edgar, "_get", lambda url, **kw: get(url, **kw) if url.endswith('/index') else '<td>Unrelated, Inc.</td>')
    assert refresh._verified_subsidiary_maps([candidate], [parent]) == []


def test_verified_subsidiary_is_persisted_and_remaps_candidate(tmp_path, monkeypatch):
    from mozes import db, refresh
    from mozes.radar import bootstrap_database
    conn = db.connect(tmp_path / 'alias.db')
    bootstrap_database(conn)
    conn.execute("INSERT INTO sponsor_ticker_map(sponsor_norm,sponsor,ticker,cik,confidence,source,updated_at) "
                 "VALUES('example','Example, Inc.','EXM','123',.9,'SEC-v2C-equity','2026-10-02')")
    conn.execute("INSERT INTO sponsor_ticker_map(sponsor_norm,sponsor,ticker,cik,confidence,source,updated_at) "
                 "VALUES('another','Another, Inc.','OTH','456',.9,'SEC-v2C-equity','2026-10-02')")
    study = {'NCTId': 'NCT00000001', 'LeadSponsorName': 'ExampleTX, Inc.', 'Phase': ['PHASE4']}
    older = {**study, 'NCTId': 'NCT00000002'}
    joint = {'NCTId': 'NCT00000003', 'LeadSponsorName': 'University Hospital',
             'CollaboratorName': ['ExampleTX, Inc.', 'Another, Inc.'],
             'CollaboratorClass': ['INDUSTRY', 'INDUSTRY']}
    store_candidates(conn, [study_to_candidate(older, []), study_to_candidate(joint, [
        {'sponsor_norm': 'another', 'sponsor': 'Another, Inc.', 'ticker': 'OTH',
         'cik': '456', 'confidence': .9}])])
    monkeypatch.setattr(refresh, 'discover', lambda **kw: ([study_to_candidate(study, kw['sponsor_map'])],
                                                          {'resume_cursor': None}))
    monkeypatch.setattr(refresh, '_verified_subsidiary_maps', lambda rows, maps, **kw: [
        {**maps[0], 'sponsor': 'ExampleTX, Inc.', 'sponsor_norm': 'exampletx',
         'source': 'SEC-v2E-subsidiary|https://www.sec.gov/exhibit21.htm'}])
    monkeypatch.setenv('SEC_USER_AGENT', 'test@example.com')
    rows, _ = refresh.discover_registry(conn, return_metadata=True)
    assert len(rows) == 3 and sum(row['ticker'] == 'EXM' and row['ticker_confidence'] >= .85 for row in rows) == 2
    assert conn.execute("SELECT ticker FROM discovery_candidates WHERE nct_id='NCT00000001'").fetchone()[0] == 'EXM'
    assert conn.execute("SELECT ticker FROM discovery_candidates WHERE nct_id='NCT00000002'").fetchone()[0] == 'EXM'
    assert conn.execute("SELECT ticker FROM discovery_candidates WHERE nct_id='NCT00000003'").fetchone()[0] is None
