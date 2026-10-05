"""Run the foreground demo or issue a command to its fixed loopback service."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

from .core import RotationEngine, RuntimeLock
from .server import DemoServer


class NoRedirects(HTTPRedirectHandler):
    """Never forward a local session or credential request to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Crystal — synthetic API-key rotation on loopback")
    parser.add_argument("command", choices=["serve", "rotate", "status", "audit", "schedule"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent.parent / ".runtime")
    parser.add_argument("--interval", type=int, default=300, help="10–86400 seconds")
    parser.add_argument("--paused", action="store_true", help="Pause the foreground scheduler")
    parser.add_argument("--failure", choices=sorted(RotationEngine.FAILURES), default="none")
    parser.add_argument("--request-id", help="Reuse the same UUID to safely retry a manual rotation")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    if args.command == "serve":
        owner = engine = server = None
        try:
            args.data_dir.mkdir(parents=True, exist_ok=True)
            owner = RuntimeLock(args.data_dir / "owner.lock")
            engine = RotationEngine(args.data_dir / "audit.sqlite3", args.interval, not args.paused)
            server = DemoServer(args.port, engine)
            scheduler = server.start_scheduler()
            print(f"Crystal is running at {server.origin}", flush=True)
            print("Synthetic credentials only. Ctrl+C stops the demo and schedule.", flush=True)
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            return 0
        except (OSError, ValueError, RuntimeError):
            print("Unable to start. Check the port, interval, data directory and runtime ownership.")
            return 1
        finally:
            if server:
                server.stop_scheduler.set()
                scheduler.join(timeout=4)
                server.server_close()
            if engine:
                engine.close()
            if owner:
                owner.close()
        return 0

    origin = f"http://127.0.0.1:{args.port}"
    # Ignore system proxies: no credential or session request leaves loopback.
    opener = build_opener(ProxyHandler({}), NoRedirects())

    def request(path: str, body: dict | None = None, token: str | None = None) -> dict | list:
        headers = {"X-Crystal-Client": "demo", "Origin": origin}
        if token:
            headers["X-Crystal-Token"] = token
            headers["Idempotency-Key"] = args.request_id or str(uuid.uuid4())
            headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        with opener.open(Request(origin + path, data, headers), timeout=5) as response:
            return json.load(response)

    try:
        if args.command in {"rotate", "schedule"}:
            token = request("/api/session")["token"]
            body = {"failure": args.failure} if args.command == "rotate" else {"interval": args.interval, "enabled": not args.paused}
            result = request("/api/" + args.command, body, token)
        else:
            result = request("/api/" + args.command)
        print(json.dumps(result, indent=2))
        return 0 if args.command != "rotate" or result.get("outcome") == "success" else 1
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        print("Command failed. Check that the demo is running; inspect status and audit for details.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
