"""Pending claims must be inspectable without changing regulatory or delivery state."""
from datetime import date
import json
import sqlite3

from mozes import db, payload_v3
from mozes.regulatory_lifecycle import apply_guidance


SOURCE = 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/ex99.htm'


def pending(conn, **overrides):
    evidence = {'application_id': 'APP-A', 'catalyst_type': 'PDUFA', 'form': '8-K',
                'filed': '2026-09-15T14:00:00Z', 'source_id': SOURCE,
                'retrieved_at': '2026-09-15T15:00:00Z',
                'statement': 'FDA approved Drug A. <script>untrusted()</script>'}
    evidence.update(overrides)
    result = apply_guidance(conn, 'AAA', evidence, today=date(2026,10,1))
    assert result['status'] == 'review'
    return evidence, result['review_id']


def catalog(conn):
    db.upsert_watch(conn, 'AAA', 'Alpha', cik='123')
    with conn:
        conn.execute("INSERT INTO events VALUES(?,'live',?)", ('APP-A', json.dumps({
            'id': 'APP-A', 'application_id': 'APP-A', 'ticker': 'AAA', 'type': 'PDUFA_NME',
            'program': 'Drug A', 'chronology': []})))
    db.upsert_event_state(conn, 'APP-A', status='SCHEDULED', verification_state='VERIFIED')


def test_review_is_exported_with_exact_evidence_without_promoting_outcome(tmp_path):
    conn = db.connect(tmp_path / 'x.db')
    payload_v3.build(conn, date(2026,10,1), consumer_only=True)
    catalog(conn)
    evidence, rid = pending(conn)
    snapshot = list(conn.iterdump())
    exported = payload_v3.build(conn, date(2026,10,1), consumer_only=True)
    assert 'regulatory_review' in exported
    queue = exported['regulatory_review']
    assert queue['count'] == len(queue['items']) == 1
    row = queue['items'][0]
    assert row['review_id'] == rid
    assert row['evidence'] == {k: evidence[k] for k in ('statement', 'catalyst_type', 'form')}
    assert row['source_url'] == SOURCE and row['published_at'] == evidence['filed']
    assert row['retrieved_at'] == evidence['retrieved_at'] and row['observed_at']
    assert row['event_timestamp'] is None
    assert row['issuer_verified'] is True and row['application']['id'] == 'APP-A'
    assert row['issuer_name'] == 'Alpha'
    assert row['verification_state'] == 'REVIEW_REQUIRED' and row['confirmed_outcome'] is False
    assert row['reason'] == 'outcome_requires_separate_reconciliation'
    assert exported['intelligence_v2c']['regulatory_review_count'] == 1
    assert list(conn.iterdump()) == snapshot


def test_projection_is_read_only_and_does_not_create_schema():
    from mozes.regulatory_review import review_queue
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    assert review_queue(conn) == {'available': False, 'count': 0, 'items': []}
    assert not conn.execute('SELECT name FROM sqlite_master').fetchall()


def test_pending_projection_does_not_verify_claimed_or_wrong_issuer_identity(tmp_path):
    from mozes.regulatory_review import review_queue
    conn = db.connect(tmp_path / 'x.db'); catalog(conn)
    first, _ = pending(conn, application_id='UNREVIEWED-APP')
    second, _ = pending(conn, source_id=SOURCE.replace('/123/', '/456/'))
    unknown, _ = pending(conn, source_id='javascript:alert(1)', filed=None)
    before = list(conn.iterdump())
    conn.execute('PRAGMA query_only=ON')
    rows = {row['source_url']: row for row in review_queue(conn)['items']}
    assert rows[SOURCE]['issuer_verified'] is True
    assert rows[SOURCE]['application'] is None
    assert rows[second['source_id']]['issuer_verified'] is False
    assert rows[second['source_id']]['application'] is None
    assert rows[None]['published_at'] is None
    assert rows[None]['issuer_verified'] is False
    assert rows[None]['event_timestamp'] is None
    assert rows[None]['evidence'] == {} and rows[None]['source_reference_withheld'] is True
    assert list(conn.iterdump()) == before


def test_duplicate_review_evidence_stays_one_item_and_all_items_remain_visible(tmp_path):
    from mozes.regulatory_review import review_queue
    conn = db.connect(tmp_path / 'x.db'); catalog(conn)
    pending(conn); pending(conn)
    for i in range(105):
        pending(conn, application_id=f'UNKNOWN-{i}', statement=f'FDA approved claim {i}.')
    queue = review_queue(conn)
    assert queue['count'] == len(queue['items']) == 106
    assert len({x['review_id'] for x in queue['items']}) == 106


def test_malformed_review_payload_retains_raw_evidence_without_crashing_export(tmp_path):
    from mozes.regulatory_review import review_queue
    conn = db.connect(tmp_path / 'x.db'); catalog(conn)
    pending(conn)
    with conn:
        conn.execute("INSERT INTO v2c_regulatory_review VALUES('BAD','AAA',?,?,'legacy',?)",
                     (SOURCE, '2026-09-01', '{broken'))
    row = next(r for r in review_queue(conn)['items'] if r['review_id'] == 'BAD')
    assert row['evidence'] == {} and row['evidence_available'] is False
    assert conn.execute("SELECT payload FROM v2c_regulatory_review WHERE review_id='BAD'").fetchone()[0] == '{broken'
    assert row['application'] is None and row['confirmed_outcome'] is False


def test_malformed_identity_fields_are_not_used_to_bind_application(tmp_path):
    from mozes.regulatory_review import review_queue
    conn = db.connect(tmp_path / 'x.db'); catalog(conn)
    pending(conn)
    bad = {'application_id': ['APP-A'], 'statement': ['FDA approved Drug A.']}
    with conn:
        conn.execute("INSERT INTO v2c_regulatory_review VALUES('BAD-ID','AAA',?,?,'legacy',?)",
                     (SOURCE, '2026-09-01', json.dumps(bad)))
    row = next(r for r in review_queue(conn)['items'] if r['review_id'] == 'BAD-ID')
    assert row['evidence'] == {} and row['application'] is None
    assert json.loads(conn.execute("SELECT payload FROM v2c_regulatory_review WHERE review_id='BAD-ID'").fetchone()[0]) == bad


def test_public_review_export_allowlists_evidence_and_preserves_private_original(tmp_path):
    from mozes.regulatory_review import review_queue
    conn = db.connect(tmp_path / 'x.db'); catalog(conn)
    original, _ = pending(conn, reviewer_notes='synthetic-private-note',
                          internal={'operator_email': 'private@example.invalid'},
                          window={'internal_trace': 'synthetic-private-trace'})
    before = list(conn.iterdump())
    row = review_queue(conn)['items'][0]
    public = json.dumps(row)
    assert 'synthetic-private' not in public and 'private@example.invalid' not in public
    assert row['evidence']['statement'] == original['statement']
    assert row['source_url'] == SOURCE
    assert list(conn.iterdump()) == before


def test_nonpublic_reference_and_malformed_raw_evidence_are_not_public(tmp_path):
    from mozes.regulatory_review import review_queue
    conn = db.connect(tmp_path / 'x.db'); catalog(conn)
    pending(conn, source_id='https://internal.example.invalid/review?token=synthetic-private',
            statement='FDA approved synthetic-private-claim')
    with conn:
        conn.execute("INSERT INTO v2c_regulatory_review VALUES('BAD-PRIVATE','AAA',?,?,'legacy',?)",
                     (SOURCE, '2026-09-01', 'synthetic-private-malformed'))
    queue = review_queue(conn)
    assert queue['count'] == len(queue['items']) == 2
    assert 'synthetic-private' not in json.dumps(queue)
    assert all(row['evidence_available'] is False for row in queue['items'])


def test_public_review_budget_refuses_entire_export_without_dropping_rows(tmp_path, monkeypatch):
    from mozes import regulatory_review
    from mozes.pipeline_v2c import export_payload
    conn = db.connect(tmp_path / 'x.db')
    payload_v3.build(conn, date(2026,10,1), consumer_only=True)
    catalog(conn); pending(conn)
    monkeypatch.setattr(regulatory_review, 'MAX_PUBLIC_REVIEW_BYTES', 1, raising=False)
    destination = tmp_path / 'site' / 'data.json'
    destination.parent.mkdir()
    destination.write_text('previous-publication', encoding='utf-8')
    import pytest
    with pytest.raises(ValueError, match='regulatory_review_public_byte_budget'):
        export_payload(conn, destination, today=date(2026,10,1), consumer={
            'checkpoint_at': '2026-10-01T00:00:00Z', 'data_age_seconds': 0, 'stale': False})
    assert destination.read_text(encoding='utf-8') == 'previous-publication'
    assert conn.execute('SELECT COUNT(*) FROM v2c_regulatory_review').fetchone()[0] == 1


def test_pages_site_budget_includes_other_assets_and_preserves_previous_export(tmp_path, monkeypatch):
    from mozes import regulatory_review
    from mozes.pipeline_v2c import export_payload
    import pytest
    conn = db.connect(tmp_path / 'x.db')
    payload_v3.build(conn, date(2026,10,1), consumer_only=True)
    catalog(conn); pending(conn)
    destination = tmp_path / 'site' / 'data.json'
    destination.parent.mkdir()
    destination.write_text('previous-publication', encoding='utf-8')
    asset = destination.parent / 'other-asset.bin'
    asset.write_bytes(b'x' * 10_000)
    monkeypatch.setattr(regulatory_review, 'MAX_PAGES_SITE_BYTES', 10_000, raising=False)
    with pytest.raises(ValueError, match='regulatory_review_pages_site_budget'):
        export_payload(conn, destination, today=date(2026,10,1), consumer={
            'checkpoint_at': '2026-10-01T00:00:00Z', 'data_age_seconds': 0, 'stale': False})
    assert destination.read_text(encoding='utf-8') == 'previous-publication'
    assert asset.stat().st_size == 10_000


def test_credential_bearing_sec_reference_is_withheld_without_hiding_review_row(tmp_path):
    from mozes.regulatory_review import review_queue
    conn = db.connect(tmp_path / 'x.db'); catalog(conn)
    pending(conn, source_id=SOURCE + '?token=synthetic-private')
    queue = review_queue(conn)
    assert queue['count'] == 1 and len(queue['items']) == 1
    assert queue['items'][0]['source_reference_withheld'] is True
    assert 'synthetic-private' not in json.dumps(queue)
