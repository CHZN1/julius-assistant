"""The three local ports stay up under connection floods and bad tokens (security plan part 4; found by scripts/fuzz_ports.py).

- A flood of idle or slow-dripping connections can't make a server start unbounded threads, and once the flood's
  connections pass the deadline a normal request works again.
- Bad-token requests to the UI channel can't use up the rate limit the orb app needs (they still get slowed down).
"""

import http.client
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import julius.ui_bridge.events as ev
import julius.ui_bridge.panelfiles as pf
import julius.ui_bridge.uichannel as uc
from julius.ui_bridge import connguard

FLOOD = 120


def flood(port, n=FLOOD, first=b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n"):
    """n connections that each send a partial request and then sit there."""
    socks = []
    for _ in range(n):
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2)
            s.sendall(first)
            socks.append(s)
        except OSError:
            pass
    return socks


def threads_now():
    return threading.active_count()


class Guarded:
    """Shared checks; subclasses start one server with a short deadline and say how to make one good request."""

    def test_a_connection_flood_does_not_grow_threads_without_bound(self):
        before = threads_now()
        socks = flood(self.port)
        time.sleep(0.5)
        try:
            self.assertLess(threads_now() - before, connguard.MAX_CONNS + 10)
        finally:
            for s in socks:
                s.close()

    def test_flood_connections_are_closed_at_the_deadline_and_service_comes_back(self):
        socks = flood(self.port)
        try:
            time.sleep(self.deadline + 1.0)
            still_open = 0
            for s in socks:
                s.settimeout(0.2)
                try:
                    s.recv(1)  # b"" or a reset: the server closed it
                except TimeoutError:
                    still_open += 1
                except OSError:
                    pass
            self.assertEqual(still_open, 0)
            self.assertTrue(self.good_request())
        finally:
            for s in socks:
                s.close()


class UiChannel(Guarded, unittest.TestCase):
    deadline = 1.0

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.token = uc.ensure_token(self.dir / "ui_token")
        self.ch = uc.Channel(
            self.token, {"typed": lambda t: {"queued": True}}, self.dir / "audit.log", port=0
        )
        self.ch.deadline_s = self.deadline
        self.ch.start()
        self.port = self.ch.port

    def tearDown(self):
        self.ch.stop()

    def ping(self, token):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(
            "GET",
            "/v1/ping",
            headers={"Host": f"127.0.0.1:{self.port}", "Authorization": "Bearer " + token},
        )
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def good_request(self):
        return self.ping(self.token) == 200

    def test_bad_tokens_cannot_lock_out_the_app(self):
        statuses = [self.ping("x" * 43) for _ in range(uc.RATE_N + 10)]
        self.assertIn(429, statuses)  # guessing is still slowed down
        self.assertTrue(all(s in (401, 429) for s in statuses))
        self.assertEqual(self.ping(self.token), 200)


class EventFeed(Guarded, unittest.TestCase):
    deadline = 1.0

    def setUp(self):
        self.hub = ev.EventHub()
        self.server = ev.EventServer(self.hub, port=0, token="t" * 32)
        self.server.handshake_deadline_s = self.deadline
        self.server.start()
        self.port = self.server.port

    def tearDown(self):
        self.server.stop()

    def good_request(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        s.sendall(
            (
                f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n"
                f"Authorization: Bearer {'t' * 32}\r\n\r\n"
            ).encode()
        )
        ok = s.recv(64).startswith(b"HTTP/1.1 101")
        s.close()
        return ok

    def test_a_slow_drip_handshake_is_cut_at_the_deadline(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        start = time.monotonic()
        try:
            while time.monotonic() - start < self.deadline + 3:
                s.sendall(b"X")
                time.sleep(0.2)
            self.fail("the server kept a dripping handshake open past its deadline")
        except OSError:
            self.assertLess(time.monotonic() - start, self.deadline + 3)
        finally:
            s.close()


class PanelFileServer(Guarded, unittest.TestCase):
    deadline = 1.0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.files = pf.PanelFiles(Path(self.tmp.name) / "p", token="tok123")
        self.ref = self.files.put(b"\x89PNG\r\n\x1a\nfake", "png")
        self.server = pf.PanelFileServer(self.files, port=0)
        self.server.httpd.deadline_s = self.deadline
        self.server.start()
        self.port = self.server.port

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def good_request(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/tok123" + self.ref, headers={"Host": f"127.0.0.1:{self.port}"})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status == 200


class Guard(unittest.TestCase):
    def test_admit_release_and_reap(self):
        g = connguard.ConnectionGuard(max_conns=2, deadline_s=0.3)
        a, b = socket.socketpair()
        c, d = socket.socketpair()
        e, f = socket.socketpair()
        try:
            self.assertTrue(g.admit(a))
            self.assertTrue(g.admit(c))
            self.assertFalse(g.admit(e))  # over the cap
            g.release(c)
            g.release(c)  # twice is fine
            self.assertTrue(g.admit(e))
            time.sleep(0.9)
            b.settimeout(1)
            self.assertEqual(b.recv(1), b"")  # a was shut down at its deadline
            self.assertEqual(g.count(), 0)
        finally:
            g.stop()
            for s in (a, b, c, d, e, f):
                s.close()


if __name__ == "__main__":
    unittest.main()


class FuzzFindings(unittest.TestCase):
    """Each case here was found by scripts/fuzz_ports.py and is now a refusal, not a server error."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.token = uc.ensure_token(self.dir / "ui_token")
        self.ch = uc.Channel(
            self.token, {"typed": lambda t: {"queued": True}}, self.dir / "audit.log", port=0
        ).start()
        self.hub = ev.EventHub()
        self.feed = ev.EventServer(self.hub, port=0, token=self.token).start()

    def tearDown(self):
        self.ch.stop()
        self.feed.stop()

    def raw(self, port, data, timeout=10):
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.sendall(data)
        got = b""
        try:
            while chunk := s.recv(4096):
                got += chunk
                if b"\r\n\r\n" in got and port == self.feed.port:
                    break
        except OSError:
            pass
        s.close()
        return got

    def typed(self, body, length=None, path="/v1/typed"):
        head = (
            f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.ch.port}\r\nAuthorization: Bearer {self.token}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body) if length is None else length}\r\n\r\n"
        )
        return self.raw(self.ch.port, head.encode() + body)

    def test_deeply_nested_json_is_a_400(self):
        # only the sprite endpoint takes a body big enough (1 MB) to nest past Python's recursion limit
        got = self.typed(b"[" * 100000 + b"]" * 100000, path="/v1/sprite")
        self.assertTrue(got.startswith(b"HTTP/1.0 400"), got[:80])

    def test_a_body_shorter_than_its_content_length_is_a_408_not_a_500(self):
        got = self.typed(b'{"text": "hi"}', length=40)
        self.assertTrue(got.startswith(b"HTTP/1.0 408"), got[:80])

    def handshake(self, *extra):
        lines = [
            "GET / HTTP/1.1",
            f"Host: 127.0.0.1:{self.feed.port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
            f"Authorization: Bearer {self.token}",
            *extra,
        ]
        return self.raw(self.feed.port, ("\r\n".join(lines) + "\r\n\r\n").encode())

    def test_the_feed_refuses_repeated_security_headers(self):
        self.assertTrue(self.handshake().startswith(b"HTTP/1.1 101"))
        for extra in (
            ("Origin: null", "Origin: http://127.0.0.1"),
            ("Host: evil.test",),
            (f"Authorization: Bearer {self.token}",),
            ("Sec-WebSocket-Protocol: julius.v1", "Sec-WebSocket-Protocol: julius.v1"),
        ):
            got = self.handshake(*extra)
            self.assertTrue(got.startswith(b"HTTP/1.1 400"), (extra, got[:60]))
