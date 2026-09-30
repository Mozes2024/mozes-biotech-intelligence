from mozes import db
from mozes.audit import audit_catalog
from mozes.radar import bootstrap_database


def test_seed_catalog_does_not_unlock_validation(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    bootstrap_database(conn)
    a = audit_catalog(conn)
    assert a["n"] > 0
    assert a["eligible"] == 0
