"""Bounded HTTP interface for the fixed loopback demonstration."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import RotationEngine

STATIC = Path(__file__).with_name("static")


class DemoServer(ThreadingHTTPServer):
    """A local development server, not a production HTTP stack."""

    daemon_threads = True
    block_on_close = True

    def __init__(self, port: int, engine: RotationEngine) -> None:
        self.engine = engine
        self.token = secrets.token_urlsafe(32)
        self.stop_scheduler = threading.Event()
        super().__init__(("127.0.0.1", port), Handler)
        self.host = f"127.0.0.1:{self.server_address[1]}"
        self.origin = f"http://{self.host}"

    def start_scheduler(self) -> threading.Thread:
        def run() -> None:
            while not self.stop_scheduler.wait(0.25):
                try:
                    self.engine.tick()
                except Exception:
                    # The engine marks unexpected rotation errors actionable.
                    # Stop the schedule without a potentially sensitive traceback.
                    self.stop_scheduler.set()

        thread = threading.Thread(target=run, name="crystal-scheduler", daemon=True)
        thread.start()
        return thread


class Handler(BaseHTTPRequestHandler):
    server: DemoServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, format: str, *args) -> None:
        # Requests can contain synthetic credentials or session authorization.
        # The allowlisted rotation audit is the only application log.
        return

    def _reply(self, status: int, payload, content_type: str = "application/json; charset=utf-8") -> None:
        body = json.dumps(payload).encode("utf-8") if isinstance(payload, (dict, list)) else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _local_request(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if hosts != [self.server.host] or len(origins) > 1 or (origins and origins[0] != self.server.origin):
            self._reply(403, {"error": "Local origin required."})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._reply(403, {"error": "Cross-site requests are disabled."})
            return False
        return True

    def do_GET(self) -> None:
        if not self._local_request():
            return
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/crystal.svg": ("crystal.svg", "image/svg+xml")}
        if self.path in assets:
            filename, kind = assets[self.path]
            self._reply(200, (STATIC / filename).read_bytes(), kind)
        elif self.path == "/api/session":
            # A custom header forces cross-origin browser callers to preflight.
            # There is no CORS allow header or OPTIONS acceptance.
            if self.headers.get("X-Crystal-Client") != "demo":
                self._reply(403, {"error": "Demo client header required."})
            else:
                self._reply(200, {"token": self.server.token})
        elif self.path == "/api/status":
            self._reply(200, self.server.engine.status())
        elif self.path == "/api/audit":
            self._reply(200, self.server.engine.audit())
        elif self.path == "/api/demo":
            with self.server.engine._lock:
                header = self.headers.get("Authorization", "")
                key = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
                accepted = len(key) <= 128 and key.isascii() and self.server.engine.provider.accepts(key)
            self._reply(200 if accepted else 401, {"authenticated": accepted})
        elif self.path == "/favicon.ico":
            self._reply(204, b"", "image/x-icon")
        else:
            self._reply(404, {"error": "Not found."})

    def do_POST(self) -> None:
        if not self._local_request():
            return
        origins = self.headers.get_all("Origin", [])
        tokens = self.headers.get_all("X-Crystal-Token", [])
        if (origins != [self.server.origin] or len(tokens) != 1
                or not tokens[0].isascii() or not secrets.compare_digest(tokens[0], self.server.token)):
            self._reply(403, {"error": "Session authorization required."})
            return
        if self.path not in {"/api/rotate", "/api/schedule"}:
            self._reply(404, {"error": "Not found."})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self._reply(400, {"error": "A bounded request body is required."})
            return
        if len(lengths[0]) > 4:
            self._reply(413, {"error": "Request too large."})
            return
        length = int(lengths[0])
        if length > 1024:
            self._reply(413, {"error": "Request too large."})
            return
        if self.headers.get("Content-Type") != "application/json":
            self._reply(415, {"error": "JSON content type required."})
            return
        try:
            body = json.loads(self.rfile.read(length))
            if type(body) is not dict:
                raise ValueError
            if self.path == "/api/rotate":
                if set(body) - {"failure"} or type(body.get("failure", "none")) is not str:
                    raise ValueError
                ids = self.headers.get_all("Idempotency-Key", [])
                if len(ids) != 1:
                    raise ValueError
                result = self.server.engine.rotate(ids[0], body.get("failure", "none"))
                self._reply(409 if result["outcome"] in {"busy", "blocked"} else 200, result)
            else:
                if set(body) != {"interval", "enabled"}:
                    raise ValueError
                self._reply(200, self.server.engine.configure(body["interval"], body["enabled"]))
        except (ValueError, TypeError, UnicodeError):
            self._reply(400, {"error": "Invalid request fields."})
        except Exception:
            # Never reflect body contents or exception strings into responses.
            self._reply(500, {"error": "The demo could not complete the operation. Restart it before retrying."})
