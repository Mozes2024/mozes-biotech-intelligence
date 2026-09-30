import sqlite3

import pytest

from mozes.db import connect
from mozes.paper import PaperBook

A = {"id": "X", "as_of": "2026-10-01", "classification": {"cls": "WATCH"},
     "date": {"window": {"start": "2030-01-01", "end": "2030-01-31"}}, "versions": {"m": "0.1.0"}}


def test_paper_signals_are_immutable(tmp_path):
    conn = connect(tmp_path / "t.db")
    book = PaperBook(conn)
    sid = book.record(A, now="2026-09-30T00:00:00+00:00")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE paper_signals SET classification='HOLD' WHERE signal_id=?", (sid,))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM paper_signals WHERE signal_id=?", (sid,))
    with pytest.raises(sqlite3.IntegrityError):
        book.record(A, now="2026-09-30T00:00:00+00:00")


def test_outcome_cannot_precede_catalyst_and_is_write_once(tmp_path):
    conn = connect(tmp_path / "t.db")
    book = PaperBook(conn)
    sid = book.record(A, now="2026-09-30T00:00:00+00:00")
    with pytest.raises(ValueError):
        book.attach_outcome(sid, "success", 0.5, today="2026-10-01")
    book.attach_outcome(sid, "success", 0.5, today="2030-02-01")
    with pytest.raises(sqlite3.IntegrityError):
        book.attach_outcome(sid, "fail", -0.5, today="2030-02-02")
    assert book.list()[0]["clinical"] == "success"
