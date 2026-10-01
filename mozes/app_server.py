"""Local product server for MOZES Biotech Catalyst Intelligence v0.3.

Uses only the Python standard library. It serves the Hebrew SPA and a small JSON API.
The default bind address is 127.0.0.1 so mutation endpoints are not exposed publicly.
"""
from __future__ import annotations

import json
import mimetypes
import threading
import uuid
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
MAX_POST_BYTES = 64 * 1024


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")


def _find_live_event(conn, event_id):
    for event in live_event_records(conn, include_quarantined=True):
        if event.get("id") == event_id:
            return event
    return None


def _web_file(path: str):
    """Return an in-boundary static target, or ``None`` for traversal attempts."""
    if path in {"/", "/index.html"}:
        return WEB_DIR / "index.html"
    candidate = (WEB_DIR / path.lstrip("/")).resolve()
    return candidate if WEB_DIR.resolve() in candidate.parents else None


class RefreshJobs:
    """Small in-process queue; refresh state itself remains persisted in refresh_runs."""
    def __init__(self, db_path):
        self.db_path, self._jobs, self._lock = db_path, {}, threading.Lock()

    def start(self, options):
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {"run_id": job_id, "status": "QUEUED"}
        thread = threading.Thread(target=self._run, args=(job_id, options), daemon=True)
        thread.start()
        return self.status(job_id)

    def status(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _run(self, job_id, options):
        with self._lock:
            self._jobs[job_id] = {"run_id": job_id, "status": "RUNNING"}
        conn = db.connect(self.db_path)
        try:
            bootstrap_database(conn)
            sec_ok = bool(SEC_USER_AGENT.strip())
            result = refresh_live(conn, do_sec_map=bool(options.get("sec_map", True)) and sec_ok,
                                  do_sec_verify=bool(options.get("sec_verify", True)) and sec_ok,
                                  start=options.get("start"), end=options.get("end"), months=options["months"])
            if not sec_ok:
                result["notice"] = "SEC_USER_AGENT is not configured; CT.gov discovery ran without SEC verification."
            job = {"run_id": job_id, "status": result.get("status", "FAILED"), "result": result}
        except Exception:
            job = {"run_id": job_id, "status": "FAILED", "error": "refresh failed; inspect persisted refresh status"}
        finally:
            conn.close()
        with self._lock:
            self._jobs[job_id] = job


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
            if n < 0 or n > MAX_POST_BYTES:
                return "too_large"
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return None

    def do_HEAD(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        if path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return
        target = _web_file(path)
        if target is None:
            self.send_error(HTTPStatus.FORBIDDEN); return
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
        if path.startswith("/api/refresh/"):
            job = self.server.refresh_jobs.status(path.rsplit("/", 1)[1])
            if not job:
                self._send_json({"error": "refresh run not found"}, 404); return
            self._send_json(job)
            return
        candidate = _web_file(path)
        if candidate is None:
            self.send_error(HTTPStatus.FORBIDDEN); return
        self._send_file(candidate)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body_json()
        if body == "too_large":
            self._send_json({"error": "request body too large"}, 413); return
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
                months = int(body.get("months", 6))
                if not 1 <= months <= 24:
                    raise ValueError
            except (TypeError, ValueError):
                self._send_json({"error": "months must be between 1 and 24"}, 400); return
            job = self.server.refresh_jobs.start({**body, "months": months})
            self._send_json({"ok": True, **job, "status_url": f"/api/refresh/{job['run_id']}"}, 202)
            return
        self._send_json({"error": "not found"}, 404)


def serve(port=8000, host="127.0.0.1", db_path=DB_PATH):
    conn = db.connect(db_path)
    bootstrap_database(conn)
    httpd = HTTPServer((host, port), Handler)
    httpd.conn = conn
    httpd.refresh_jobs = RefreshJobs(db_path)
    print(f"MOZES v0.3: http://{host}:{port}")
    print("API: /api/radar  ·  /api/status  ·  /api/health")
    try:
        httpd.serve_forever()
    finally:
        conn.close()
        httpd.server_close()
