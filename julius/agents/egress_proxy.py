"""Allowlist egress proxy for the coding agent: the sandboxed `claude` process can reach api.anthropic.com and nothing else.

How it fits together (julius_code_agent.py wires it):
  * the proxy runs OUTSIDE the sandbox on 127.0.0.1 with an OS-chosen port, only while a coding task runs;
  * the seatbelt profile denies all network except a connection to that one local port (no direct 443, no DNS);
  * the child gets HTTPS_PROXY/HTTP_PROXY pointing at it, so every request it makes arrives here as `CONNECT host:port`.
The proxy accepts CONNECT to an exact (host, port) in the allowlist and refuses everything else with 403 (plain HTTP verbs get 405).
It resolves the name itself and refuses non-public addresses, then splices bytes without looking inside (TLS stays end to end).
Every refusal is appended to a log file (host and port only, never payloads) so a blocked run can be explained."""

import contextlib
import ipaddress
import select
import socket
import socketserver
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ALLOWED = (("api.anthropic.com", 443),)
HEADER_LIMIT = 8192
IDLE_SECONDS = 120


def _public(ip: Any) -> Any:
    a = ipaddress.ip_address(ip.split("%")[0])
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


class EgressProxy:
    def __init__(
        self,
        allowed: tuple[tuple[str, int]] = ALLOWED,
        log_path: Path | None = None,
        resolve: Callable = socket.getaddrinfo,
        dial: Callable | None = None,
    ) -> None:
        self.allowed = {(h.lower(), int(p)) for h, p in allowed}
        self.log_path, self.resolve = log_path, resolve
        self.dial = dial or (lambda ip, port: socket.create_connection((ip, port), 10))
        self.blocked, self.allowed_count, self.server = [], 0, None
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return self.server.server_address[1] if self.server else None

    def _log(self, line: str) -> None:
        with self._lock:
            self.blocked.append(line)
        if self.log_path:
            try:
                with open(self.log_path, "a") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
            except OSError:
                pass

    def start(self) -> "EgressProxy":
        proxy = self

        class H(socketserver.BaseRequestHandler):
            def handle(self) -> Any:
                s = self.request
                s.settimeout(10)
                try:
                    data = b""
                    while b"\r\n\r\n" not in data and len(data) < HEADER_LIMIT:
                        chunk = s.recv(4096)
                        if not chunk:
                            return
                        data += chunk
                    first = data.split(b"\r\n", 1)[0].decode("latin-1", "replace").split()
                    if len(first) < 2 or first[0].upper() != "CONNECT":
                        proxy._log(f"refused method {first[0][:10] if first else '?'}")
                        return self._say(405, "Only CONNECT to allowed hosts")
                    host, _, port = first[1].rpartition(":")
                    try:
                        port = int(port)
                    except ValueError:
                        proxy._log("refused malformed target")
                        return self._say(400, "Bad target")
                    host = host.strip("[]").lower().rstrip(".")
                    if (host, port) not in proxy.allowed:
                        proxy._log(f"blocked {host[:100]}:{port}")
                        return self._say(403, "Host not allowed")
                    try:
                        ips = [i[4][0] for i in proxy.resolve(host, port, type=socket.SOCK_STREAM)]
                        if not ips or not all(_public(ip) for ip in ips):
                            proxy._log(f"blocked non-public address for {host}")
                            return self._say(403, "Address not allowed")
                        upstream = proxy.dial(ips[0], port)
                    except OSError:
                        proxy._log(f"unreachable {host}:{port}")
                        return self._say(502, "Upstream unreachable")
                    with proxy._lock:
                        proxy.allowed_count += 1
                    s.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                    s.settimeout(None)
                    rest = data.split(b"\r\n\r\n", 1)[1]
                    if rest:
                        upstream.sendall(rest)
                    self._splice(s, upstream)
                except OSError:
                    pass

            def _say(self, code: Any, why: Any) -> None:
                with contextlib.suppress(OSError):
                    self.request.sendall(
                        f"HTTP/1.1 {code} {why}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
                    )

            def _splice(self, a: Any, b: Any) -> None:
                socks = [a, b]
                try:
                    while True:
                        r, _, _ = select.select(socks, [], [], IDLE_SECONDS)
                        if not r:
                            return
                        for src in r:
                            chunk = src.recv(65536)
                            if not chunk:
                                return
                            (b if src is a else a).sendall(chunk)
                finally:
                    b.close()

        class S(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = S(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="egress-proxy").start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def env(self) -> dict[str, str]:
        url = f"http://127.0.0.1:{self.port}"
        return {
            "HTTPS_PROXY": url,
            "HTTP_PROXY": url,
            "https_proxy": url,
            "http_proxy": url,
            "ALL_PROXY": url,
            "NO_PROXY": "",
            "no_proxy": "",
        }
