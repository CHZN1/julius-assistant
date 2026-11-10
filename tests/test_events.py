from julius.paths import REPO_ROOT
import json, socket, threading, time, unittest

import julius.ui_bridge.events as ev
import julius.ui_bridge.events_client as cl


def wait(pred, t=3.0):
    end = time.time() + t
    while time.time() < end:
        if pred(): return True
        time.sleep(0.01)
    return False


class Feed(unittest.TestCase):
    def setUp(self):
        self.hub = ev.EventHub(); self.server = ev.EventServer(self.hub, port=0).start(); self.socks = []

    def tearDown(self):
        for s in self.socks: s.close()
        self.server.stop()

    def client(self):
        s, rest = cl.connect(self.server.port); self.socks.append(s); g = cl.events(s, rest); return s, g

    def test_listens_on_localhost_only(self):
        self.assertEqual(self.server._sock.getsockname()[0], "127.0.0.1")

    def test_hello_state_transcript_and_level_are_broadcast(self):
        self.hub.say("you", "what's the weather", 1)
        s, g = self.client(); hello = next(g)
        self.assertEqual((hello["type"], hello["state"]), ("hello", "idle")); self.assertEqual(hello["transcript"][0]["text"], "what's the weather")
        self.assertTrue(wait(lambda: self.hub.clients == 1))
        self.hub.set_state("thinking"); self.hub.say("julius", "It is sunny.", 1); self.hub.set_level(0.1); self.hub.set_level(0.0)
        got = [next(g) for _ in range(4)]
        self.assertEqual(got[0], {"type": "state", "state": "thinking", "prev": "idle", "t": got[0]["t"]})
        self.assertEqual((got[1]["who"], got[1]["text"], got[1]["turn"]), ("julius", "It is sunny.", 1))
        self.assertAlmostEqual(got[2]["level"], 0.4, places=2); self.assertEqual(got[3]["level"], 0.0, "one closing zero")

    def test_unchanged_state_and_silence_send_nothing(self):
        s, g = self.client(); next(g); self.assertTrue(wait(lambda: self.hub.clients == 1))
        self.hub.set_state("idle"); self.hub.set_level(0.0); self.hub.set_state("bogus"); self.hub.say("you", "  ")
        self.hub.set_state("listening"); self.assertEqual(next(g)["state"], "listening")

    def test_client_messages_are_ignored_and_ping_is_answered(self):
        s, g = self.client(); next(g); self.assertTrue(wait(lambda: self.hub.clients == 1))
        mask = b"\x01\x02\x03\x04"; body = b'{"state":"asleep"}'
        s.sendall(b"\x81" + bytes([0x80 | len(body)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(body)))
        s.sendall(b"\x89\x80" + mask)   # ping
        time.sleep(0.2); self.assertEqual(self.hub.state, "idle", "a client cannot change anything")
        s.settimeout(2); self.assertEqual(s.recv(2)[0] & 0x0F, 0xA, "pong")

    def test_foreign_origin_and_host_are_refused(self):
        for hdrs in ("Origin: http://evil.example\r\n", "Host: evil.example\r\n"):
            s = socket.create_connection(("127.0.0.1", self.server.port)); self.socks.append(s)
            host = "Host: 127.0.0.1\r\n" if "Host" not in hdrs else ""
            s.sendall(("GET / HTTP/1.1\r\n" + host + hdrs + "Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            self.assertIn(b" 403 ", s.recv(200).split(b"\r\n")[0])
        self.assertEqual(self.hub.clients, 0)
        s = socket.create_connection(("127.0.0.1", self.server.port)); self.socks.append(s)
        s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: http://localhost:3000\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.assertIn(b" 101 ", s.recv(300).split(b"\r\n")[0])

    def test_plain_http_is_refused(self):
        s = socket.create_connection(("127.0.0.1", self.server.port)); self.socks.append(s); s.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        self.assertIn(b" 426 ", s.recv(100))

    def test_quiet_is_a_distinct_state(self):
        self.assertIn("quiet", ev.STATES); self.assertIn("asleep", ev.STATES)
        self.hub.set_state("asleep"); self.hub.set_state("quiet"); self.assertEqual(self.hub.state, "quiet")
        self.assertIn("'quiet'", open(REPO_ROOT / "julius/runtime.py").read())

    def test_accept_key_matches_rfc6455_example(self):
        self.assertEqual(ev.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_long_messages_use_extended_length(self):
        f = ev.frame("x" * 300); self.assertEqual(f[1], 126); self.assertEqual(int.from_bytes(f[2:4], "big"), 300)

    def test_state_and_level_follow_the_sampler(self):
        st = {"s": "idle", "l": 0.0}; srv = ev.EventServer(self.hub, port=0, state_fn=lambda: st["s"], level_fn=lambda: st["l"], hz=100).start()
        try:
            s, rest = cl.connect(srv.port); self.socks.append(s); g = cl.events(s, rest); next(g)
            st["s"] = "speaking"; st["l"] = 0.2
            seen = {}
            for _ in range(10):
                e = next(g); seen[e["type"]] = e
                if len(seen) >= 2: break
            self.assertEqual(seen["state"]["state"], "speaking"); self.assertAlmostEqual(seen["level"]["level"], 0.8, places=2)
        finally: srv.stop()

    def test_level_tap(self):
        import numpy as np
        now = [0.0]; t = ev.LevelTap(clock=lambda: now[0]); t.out(np.ones(100, dtype=np.float32) * 0.5, 24000)
        self.assertAlmostEqual(t.level(), 0.5, places=3); now[0] = 1.0; self.assertEqual(t.level(), 0.0, "decays when nothing is written")

    def test_disabled_and_port_in_use(self):
        self.assertEqual(ev.start_from_config({"enabled": False}), (None, None))
        self.assertEqual(ev.start_from_config({"enabled": True, "port": self.server.port}), (None, None))


class Wiring(unittest.TestCase):
    def test_candidate_publishes(self):
        src = open(REPO_ROOT / "julius/runtime.py").read()
        for needle in ("from julius.ui_bridge import events", "_events_hub.say(", "_ui_state"): self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
