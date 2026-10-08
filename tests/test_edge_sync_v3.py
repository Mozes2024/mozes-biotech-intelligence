import json

from mozes import db
from mozes.edge_sync import build_universe, sync


def test_edge_sync_uses_stored_cik_and_never_posts_without_token(tmp_path):
    conn = db.connect(tmp_path / "edge.db")
    conn.execute("INSERT INTO sponsor_ticker_map VALUES(?,?,?,?,?,?,?)",
                 ("novel", "Novel Bio", "ZZZZ", "123", .99, "SEC-v2C-equity", "2026-10-08"))
    assert build_universe(conn)["issuers"][0]["cik"] == "123"
    assert sync(conn, url="https://edge.example/edge/sync", token="")["status"] == "SKIPPED"

    calls = []
    class Reply:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    def opener(request, timeout):
        calls.append((json.loads(request.data), timeout))
        assert request.get_header("Authorization") == "Bearer secret"
        return Reply()
    assert sync(conn, url="https://edge.example/edge/sync", token="secret", opener=opener) == {"status": "OK", "count": 1}
    assert calls[0][0]["issuers"][0]["ticker"] == "ZZZZ"
