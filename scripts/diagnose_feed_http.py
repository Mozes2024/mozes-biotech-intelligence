"""Isolated read-only diagnostic; never invoke eligibility, claim or processing."""
import contextlib
import io
import json
import os
import sys
from pathlib import Path
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parent))
import incident_recovery as incident

HOST = "mozes-hot-clock.sp500.workers.dev"
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def category(code):
    if 300 <= code < 400: return "redirect"
    if code in (401, 403, 404, 429): return "http_" + str(code)
    if code >= 500: return "http_5xx"
    return "http_other"

def emit(label, result, reason, status=None):
    print(label + " " + result + " " + reason + " status=" + (str(status) if status else "none"))

def feed(label, user_agent=None):
    request = urllib.request.Request("https://" + HOST + "/alerts") if user_agent else "https://" + HOST + "/alerts"
    if user_agent: request.add_header("User-Agent", user_agent)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            status = response.status
            raw = response.read(262145)
        value = json.loads(raw) if len(raw) <= 262144 else None
        valid = (isinstance(value, dict) and value.get("schema") == 1
                 and isinstance(value.get("alerts"), list) and len(value["alerts"]) == 40
                 and value.get("revision") == "246b6cd8797afa35a7986eba94f102d1f0975b552bd4af6dfc9b9f529587b73c")
        emit(label, "PASS" if valid else "FAIL", "validated" if valid else "response_shape_failure", status)
        return valid
    except urllib.error.HTTPError as exc:
        emit(label, "FAIL", category(exc.code), exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        emit(label, "FAIL", "network_or_timeout")
    except (ValueError, TypeError):
        emit(label, "FAIL", "response_shape_failure")
    return False

def main():
    incident.validate_verification_configuration()
    parts = urlsplit(os.environ["MOZES_EDGE_SYNC_URL"])
    if parts.scheme != "https" or parts.netloc != HOST or parts.username or parts.password:
        emit("public_feed", "FAIL", "approved_origin_mismatch")
        return
    first = feed("public_feed_default")
    second = feed("public_feed_lineage_audit", "MOZES-LineageAudit/1.0")
    if not (first and second): return
    # Observe sanitized status before reviewed transports suppress exceptions.
    original = urllib.request.OpenerDirector.open
    last = [None]
    def observed(self, *args, **kwargs):
        try: return original(self, *args, **kwargs)
        except urllib.error.HTTPError as exc:
            last[0] = exc.code
            raise
    urllib.request.OpenerDirector.open = observed
    # Existing provider transport retains its exact request allowlist. Edge
    # transports deny redirects; also deny redirects in durable feed auditing.
    urllib.request.install_opener(urllib.request.build_opener(NoRedirect()))
    now = datetime.now(timezone.utc)
    phases = [("provider", lambda: incident.provider_gate(now)),
              ("pending_edge_checks", incident.pending_gate),
              ("durable_history", lambda: incident.restore.restore(incident.SOURCE, recovery_sha256=incident.SHA256, verify_only=True, now=now))]
    for label, action in phases:
        last[0] = None
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = action()
            emit(label, "PASS", "validated")
            if label == "provider":
                print("capacity_demonstrated=" + str(result["capacity_demonstrated"]).lower())
        except Exception:
            emit(label, "FAIL", category(last[0]) if last[0] else "validation_or_transport_failure", last[0])
            return
if __name__ == "__main__":
    try: main()
    except Exception: emit("configuration", "FAIL", "validation_failure")
