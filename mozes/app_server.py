"""Local product server for MOZES Biotech Catalyst Intelligence v0.3.

Uses only the Python standard library. It serves the Hebrew SPA and a small JSON API.
The default bind address is 127.0.0.1 so mutation endpoints are not exposed publicly.
"""
from __future__ import annotations

import json
import mimetypes
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import db
from .config import DB_PATH, SEC_USER_AGENT
from .engine_v2 import score_event
from .paper import PaperBook
from .payload_v3 import build
from .radar import bootstrap_database, live_event_records
from .refresh import refresh_live

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def _find_live_event(conn, event_id):
    for event in live_event_records(conn, include_quarantined=True):
        if event.get("id") == event_id:
            return event
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "MOZES/0.3"

    @property
    def conn(self):
        return self.server.conn

    def log_message(self, fmt, *args):
        # Keep CLI output calm; errors still surface through responses.
        return

    def _send_json(self, value, status=200):
        body = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path):
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix in {".html", ".css", ".js", ".json"}:
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body_json(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return None

    def do_HEAD(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        if path in {"/", "/index.html"}:
            target = WEB_DIR / "index.html"
        elif path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return
        else:
            target = (WEB_DIR / path.lstrip("/")).resolve()
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND); return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        q = parse_qs(u.query)
        if path == "/api/health":
            bootstrap_database(self.conn)
            self._send_json({"ok": True, "version": "0.3.0", "db": str(DB_PATH), "events": db.count_events(self.conn)})
            return
        if path == "/api/radar":
            try:
                today = date.fromisoformat(q.get("as_of", [date.today().isoformat()])[0])
            except ValueError:
                self._send_json({"error": "invalid as_of date"}, 400); return
            self._send_json(build(self.conn, today))
            return
        if path.startswith("/api/events/"):
            event_id = path.split("/api/events/", 1)[1]
            event = _find_live_event(self.conn, event_id)
            if not event:
                self._send_json({"error": "event not found"}, 404); return
            as_of = q.get("as_of", [date.today().isoformat()])[0]
            self._send_json(score_event(self.conn, event, as_of))
            return
        if path == "/api/paper":
            bootstrap_database(self.conn)
            self._send_json({"signals": PaperBook(self.conn).list()})
            return
        if path == "/api/status":
            payload = build(self.conn, date.today())
            self._send_json({
                "version": payload["version"], "generated_at": payload["generated_at"],
                "summary": payload["summary"], "validation": payload["validation"], "refresh": payload["refresh"],
                "watch_universe": payload["watch_universe"],
            })
            return
        if path in {"/", "/index.html"}:
            self._send_file(WEB_DIR / "index.html")
            return
        # Static assets are constrained to WEB_DIR.
        rel = path.lstrip("/")
        candidate = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in candidate.parents:
            self.send_error(HTTPStatus.FORBIDDEN); return
        self._send_file(candidate)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body_json()
        if body is None:
            self._send_json({"error": "invalid JSON"}, 400); return
        if u.path == "/api/paper":
            event_id = body.get("event_id")
            event = _find_live_event(self.conn, event_id)
            if not event:
                self._send_json({"error": "event not found"}, 404); return
            as_of = body.get("as_of") or date.today().isoformat()
            analysis = score_event(self.conn, event, as_of)
            sid = PaperBook(self.conn).record(analysis, price=body.get("price"))
            self._send_json({"ok": True, "signal_id": sid}, 201)
            return
        if u.path == "/api/refresh":
            try:
                sec_ok = bool(SEC_USER_AGENT.strip())
                result = refresh_live(
                    self.conn,
                    start=body.get("start"), end=body.get("end"), months=int(body.get("months", 6)),
                    do_sec_map=bool(body.get("sec_map", True)) and sec_ok,
                    do_sec_verify=bool(body.get("sec_verify", True)) and sec_ok,
                )
                if not sec_ok:
                    result["notice"] = "SEC_USER_AGENT is not configured; CT.gov discovery ran without SEC verification."
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, 500); return
            self._send_json({"ok": result.get("status") == "OK", "result": result}, 200)
            return
        self._send_json({"error": "not found"}, 404)


def serve(port=8000, host="127.0.0.1", db_path=DB_PATH):
    conn = db.connect(db_path)
    bootstrap_database(conn)
    httpd = HTTPServer((host, port), Handler)
    httpd.conn = conn
    print(f"MOZES v0.3: http://{host}:{port}")
    print("API: /api/radar  ·  /api/status  ·  /api/health")
    try:
        httpd.serve_forever()
    finally:
        conn.close()
        httpd.server_close()
