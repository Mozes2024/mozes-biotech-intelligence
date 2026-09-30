from mozes import db
from mozes.engine_v2 import recommendation_status
from mozes.radar import bootstrap_database, current_resolved_records, live_event_records
from mozes.security import tradability


def test_aclx_is_acquired_suppressed_and_asset_maps_to_gild(tmp_path):
    conn = db.connect(tmp_path / "x.db"); bootstrap_database(conn)
    assert tradability(conn, "ACLX")["status"] == "ACQUIRED"
    assert "ACLX" not in {x["ticker"] for x in live_event_records(conn)}
    assert any(x["ticker"] == "ACLX" for x in current_resolved_records(conn))
    owner = conn.execute("SELECT owner_ticker FROM asset_ownership WHERE asset_id='anito-cel'").fetchone()
    assert owner["owner_ticker"] == "GILD"


def test_nontradable_unknown_and_renamed_are_fail_safe(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    for ticker, status in (("OLD", "DELISTED"), ("STOP", "SUSPENDED"), ("NEW", "RENAMED")):
        db.upsert_security_lifecycle(conn, ticker, status=status, source_url="https://sec.test", source_type="sec", verified_at="2026-01-01T00:00:00Z")
    assert not tradability(conn, "OLD")["tradable"]
    assert not tradability(conn, "MISSING")["tradable"]
    rec = recommendation_status({"verification_state":"VERIFIED"}, {"score":90}, {"score":90}, [], {"available":True}, {"class":"REVIEW"}, {}, tradability(conn, "MISSING"))
    assert rec["status"] == "INSUFFICIENT_INFORMATION"


def test_corporate_action_update_is_idempotent_and_does_not_inherit_score(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    args = dict(status="ACQUIRED", acquirer_ticker="BUY", source_url="https://sec.test", source_type="sec", verified_at="2026-01-01T00:00:00Z")
    db.upsert_security_lifecycle(conn, "OLD", **args); db.upsert_security_lifecycle(conn, "OLD", **args)
    assert conn.execute("SELECT COUNT(*) FROM security_lifecycle WHERE ticker='OLD'").fetchone()[0] == 1
    assert recommendation_status({"verification_state":"VERIFIED"}, {"score":99}, {"score":99}, [], {"available":True}, {"class":"REVIEW"}, {}, tradability(conn, "OLD"))["status"] == "INSUFFICIENT_INFORMATION"
