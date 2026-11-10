"""UI write channel: the one way the orb app talks TO Julius (the event feed on 8765 stays read-only). Documented in docs/UI_CHANNEL.md.

A tiny HTTP server on 127.0.0.1 (default port 8775), JSON in, JSON out, no third-party packages.
  POST /v1/typed   {"text": "..."}                  text typed in the orb; goes through the same pipeline as speech
  POST /v1/drop    {"paths": ["/abs/path", ...]}    files dropped on the orb; staged only (see julius_dropstage.py), nothing runs
  GET  /v1/ping                                     authenticated liveness check

Safety, in the order the checks run:
  1. binds 127.0.0.1 only; Host must be 127.0.0.1/localhost with this port (DNS rebinding)
  2. any Origin header is refused: the native app sends none, a web page always does
  3. rate limit (all requests, failures included, so guessing the token is slow): 20 per 10 s
  4. Authorization: Bearer <per-install token> (~/.julius/ui_token, created with mode 0600), constant-time compare
  5. body at most 16 KiB, Content-Type application/json, valid JSON object
  6. endpoint validation: typed text 1..2000 chars and never a slash command (/quit and /stop are keyboard commands, not UI commands); at most 50 paths, strings only
Every request is appended to ~/.julius/ui_audit.log as one JSON line (time, endpoint, status, sizes; never the token and never the typed text)."""

import collections
import hmac
import http.server
import json
import os
import secrets
import socketserver
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from julius import log

HOST = "127.0.0.1"
DEFAULT_PORT = 8775
MAX_BODY = 16 * 1024
MAX_TEXT = 2000
MAX_PATHS = 50
MAX_PATH_LEN = 4096
RATE_N, RATE_S = 20, 10.0
HOME = Path.home() / ".julius"


class TypedText(str):
    """A typed line that came from the UI (so the reply can be text-only when ui.typed_reply = text)."""

    from_ui = True


def ensure_token(path: Path | None = None) -> str:
    """Read the per-install token, creating it (mode 0600) on first use. A token file that others could read is tightened first."""
    path = Path(path or HOME / "ui_token")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    elif path.stat().st_mode & 0o077:
        os.chmod(path, 0o600)
    token = path.read_text().strip()
    if len(token) < 20:
        raise ValueError(f"{path} is too short to be a token; delete it to have a new one made")
    return token


class _Limiter:
    def __init__(
        self, n: int = RATE_N, seconds: int | float = RATE_S, clock: Callable = time.monotonic
    ) -> None:
        self.n, self.seconds, self.clock = n, seconds, clock
        self.hits = collections.deque()
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = self.clock()
            while self.hits and now - self.hits[0] > self.seconds:
                self.hits.popleft()
            if len(self.hits) >= self.n:
                return False
            self.hits.append(now)
            return True


class Refused(Exception):
    def __init__(self, status: Any, why: Any) -> None:
        super().__init__(why)
        self.status, self.why = status, why


def validate_typed(body: Any) -> Any:
    text = body.get("text")
    if not isinstance(text, str):
        raise Refused(400, "text must be a string")
    text = " ".join(
        text.replace("\r", "\n").split()
    )  # newlines and runs of spaces become single spaces
    if not text:
        raise Refused(400, "text is empty")
    if len(text) > MAX_TEXT:
        raise Refused(413, f"text is longer than {MAX_TEXT} characters")
    if text.startswith("/"):
        raise Refused(400, "slash commands cannot be sent from the UI")
    if any(ord(c) < 32 for c in text):
        raise Refused(400, "control characters are not allowed")
    return text


def validate_paths(body: Any) -> Any:
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths:
        raise Refused(400, "paths must be a non-empty list")
    if len(paths) > MAX_PATHS:
        raise Refused(413, f"at most {MAX_PATHS} paths")
    out = []
    for p in paths:
        if not isinstance(p, str) or not p or len(p) > MAX_PATH_LEN or "\x00" in p:
            raise Refused(400, "each path must be a string")
        if not p.startswith("/"):
            raise Refused(400, "paths must be absolute")
        out.append(p)
    return out


class Channel:
    """handlers: {"typed": fn(text) -> dict, "drop": fn(paths) -> dict}. Handlers get validated input only."""

    def __init__(
        self,
        token: str,
        handlers: dict[str, Callable],
        audit_path: Path | None = None,
        port: int = DEFAULT_PORT,
        limiter: _Limiter | None = None,
    ) -> None:
        self.token, self.handlers, self.port = token, handlers, port
        self.audit_path = Path(audit_path) if audit_path else HOME / "ui_audit.log"
        self.limiter = limiter or _Limiter()
        self.httpd = None
        self._audit_lock = threading.Lock()

    def audit(self, endpoint: Any, status: Any, **fields) -> None:
        row = {"t": round(time.time(), 3), "endpoint": endpoint, "status": status, **fields}
        try:
            with self._audit_lock:
                self.audit_path.parent.mkdir(parents=True, exist_ok=True)
                new = not self.audit_path.exists()
                with self.audit_path.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                if new:
                    os.chmod(self.audit_path, 0o600)
        except OSError:
            pass

    def handle(self, method: Any, path: Any, headers: Any, body_reader: Any) -> Any:
        """Returns (status, dict). Raises nothing; every refusal is a Refused turned into a response by the caller."""
        self._authenticate(headers)
        if method == "GET" and path == "/v1/ping":
            return 200, {"ok": True}
        if method != "POST" or path not in ("/v1/typed", "/v1/drop"):
            raise Refused(404, "no such endpoint")
        body = self._read_json(headers, body_reader)
        if path == "/v1/typed":
            text = validate_typed(body)
            log.event("ui_typed", stage="received", chars=len(text))  # never the text itself
            res = self._handler("typed", "typing is not available")(text)
            self.audit("typed", 200, chars=len(text))
        else:
            paths = validate_paths(body)
            res = self._handler("drop", "drops are not available")(paths)
            self.audit("drop", 200, paths=len(paths))
        return 200, {"ok": True, **(res or {})}

    def _authenticate(self, headers: Any) -> None:
        """Local host, no browser Origin, under the rate limit, and the right bearer token."""
        host = (headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            raise Refused(403, "bad Host")
        if headers.get("Origin") is not None:
            raise Refused(403, "Origin not allowed")
        if not self.limiter.allow():
            raise Refused(429, "slow down")
        auth = headers.get("Authorization") or ""
        if not (
            auth.startswith("Bearer ")
            and hmac.compare_digest(auth[7:].strip().encode(), self.token.encode())
        ):
            raise Refused(401, "bad token")

    @staticmethod
    def _read_json(headers: Any, body_reader: Any) -> dict:
        if (headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise Refused(415, "Content-Type must be application/json")
        try:
            length = int(headers.get("Content-Length") or "")
        except ValueError:
            raise Refused(411, "Content-Length required") from None
        if length < 0 or length > MAX_BODY:
            raise Refused(413, "body too large")
        try:
            body = json.loads(body_reader(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise Refused(400, "body is not JSON") from None
        if not isinstance(body, dict):
            raise Refused(400, "body must be an object")
        return body

    def _handler(self, name: str, unavailable: Any) -> Any:
        handler = self.handlers.get(name)
        if handler is None:
            raise Refused(503, unavailable)
        return handler

    def start(self) -> "Channel":
        chan = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            server_version = "Julius"
            timeout = 5  # a client that stalls mid-request is dropped

            def log_message(self, *a) -> None:
                pass

            def _go(self, method: Any) -> None:
                try:
                    status, out = chan.handle(method, self.path, self.headers, self.rfile.read)
                except Refused as r:
                    status, out = r.status, {"ok": False, "error": r.why}
                    log.event(
                        "ui_request_refused",
                        endpoint=self.path.split("?")[0][:40],
                        status=status,
                        why=r.why,
                    )
                    chan.audit(self.path.split("?")[0][:40], status, why=r.why)
                except (
                    Exception
                ) as e:  # a handler bug must not take the server down or leak internals
                    status, out = 500, {"ok": False, "error": "internal error"}
                    log.event(
                        "ui_request_failed",
                        endpoint=self.path.split("?")[0][:40],
                        error=type(e).__name__,
                    )
                    chan.audit(self.path.split("?")[0][:40], 500, why=type(e).__name__)
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._go("GET")

            def do_POST(self) -> None:
                self._go("POST")

            def do_PUT(self) -> None:
                self._go("PUT")

            def do_DELETE(self) -> None:
                self._go("DELETE")

            def do_PATCH(self) -> None:
                self._go("PATCH")

            def do_OPTIONS(self) -> None:
                self._go("OPTIONS")  # no CORS: a browser preflight gets a refusal

        class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.httpd = S((HOST, self.port), H)
        self.port = self.httpd.server_address[1]
        self.httpd.timeout = 5
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="ui-channel").start()
        return self

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def start_from_config(
    cfg: dict[str, bool | int],
    handlers: dict[Any, Any],
    token_path: Path | None = None,
    audit_path: Path | None = None,
) -> Any:
    """cfg is the [ui] table. Returns the running Channel, or None when disabled or the port is taken (Julius never fails to start over the UI channel)."""
    if not (cfg or {}).get("enabled", True):
        return None
    try:
        ch = Channel(
            ensure_token(token_path),
            handlers,
            audit_path,
            int((cfg or {}).get("port", DEFAULT_PORT)),
        )
        return ch.start()
    except (OSError, ValueError):
        return None
