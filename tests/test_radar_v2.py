from pathlib import Path

from mozes import db
from mozes.radar import bootstrap_database, current_resolved_records, live_event_records, validation_status


def test_bootstrap_excludes_resolved_early_phar_and_quarantines_regn(tmp_path):
    conn = db.connect(tmp_path / "v2.db")
    bootstrap_database(conn)
    live = {x["id"]: x for x in live_event_records(conn, include_quarantined=True)}
    resolved = {x["id"]: x for x in current_resolved_records(conn)}
    assert "PHAR-SNDA" not in live
    assert resolved["PHAR-SNDA"]["state"]["status"] == "APPROVED"
    assert live["REGN-POZELIMAB"]["state"]["status"] == "QUARANTINED"


def test_both_trading_gates_locked_by_default(tmp_path):
    conn = db.connect(tmp_path / "v2.db")
    bootstrap_database(conn)
    g = validation_status(conn)
    assert not g["runup"]["satisfied"]
    assert not g["hold_through"]["satisfied"]
