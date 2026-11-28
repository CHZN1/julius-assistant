"""Unit tests for uichannel."""

import http.client
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import julius.ui_bridge.uichannel as uc


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.token = uc.ensure_token(self.dir / "ui_token")
        self.got = {"typed": [], "drop": []}
        self.ch = uc.Channel(
            self.token,
            {
                "typed": lambda t: self.got["typed"].append(t) or {"queued": True},
                "drop": lambda p: self.got["drop"].append(p) or {"staged": len(p)},
            },
            audit_path=self.dir / "audit.log",
            port=0,
        ).start()

    def tearDown(self):
        self.ch.stop()

    def req(
        self,
        method="POST",
        path="/v1/typed",
        body=None,
        headers=None,
        raw=None,
        auth=True,
        host=None,
    ):
        c = http.client.HTTPConnection("127.0.0.1", self.ch.port, timeout=5)
        h = {"Content-Type": "application/json"}
        if auth:
            h["Authorization"] = "Bearer " + self.token
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c.putrequest(method, path, skip_host=True)
        c.putheader("Host", host or f"127.0.0.1:{self.ch.port}")
        for k, v in h.items():
            if v is not None:
                c.putheader(k, v)
        if data is not None:
            c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        out = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, out


class Token(unittest.TestCase):
    def test_created_0600_and_tightened_if_loose(self):
        p = Path(tempfile.mkdtemp()) / "sub" / "ui_token"
        t = uc.ensure_token(p)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertGreaterEqual(len(t), 32)
        os.chmod(p, 0o644)
        self.assertEqual(uc.ensure_token(p), t)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)

    def test_a_stub_token_is_refused(self):
        p = Path(tempfile.mkdtemp()) / "ui_token"
        p.write_text("abc\n")
        os.chmod(p, 0o600)
        with self.assertRaises(ValueError):
            uc.ensure_token(p)


class Channel(Base):
    def test_binds_localhost_only(self):
        self.assertEqual(self.ch.httpd.server_address[0], "127.0.0.1")

    def test_typed_text_reaches_the_handler_cleaned(self):
        s, out = self.req(body={"text": "  what's   the\nweather  "})
        self.assertEqual(s, 200)
        self.assertTrue(out["queued"])
        self.assertEqual(self.got["typed"], ["what's the weather"])

    def test_a_typed_request_is_logged_as_received_without_its_text(self):
        from unittest.mock import patch

        with patch("julius.ui_bridge.uichannel.log.event") as event:
            self.req(body={"text": "what's the weather in springfield"})
        event.assert_any_call(
            "ui_typed", stage="received", chars=len("what's the weather in springfield")
        )
        self.assertNotIn("springfield", repr(event.call_args_list))

    def test_refused_requests_are_logged(self):
        from unittest.mock import patch

        with patch("julius.ui_bridge.uichannel.log.event") as event:
            self.req(body={"text": "hi"}, auth=False)
        names = [c.args[0] for c in event.call_args_list]
        self.assertIn("ui_request_refused", names)

    def test_token_is_required_and_wrong_tokens_fail(self):
        self.assertEqual(self.req(body={"text": "hi"}, auth=False)[0], 401)
        self.assertEqual(
            self.req(body={"text": "hi"}, headers={"Authorization": "Bearer nope"})[0], 401
        )
        self.assertEqual(
            self.req(body={"text": "hi"}, headers={"Authorization": self.token})[0],
            401,
            "must be a Bearer header",
        )
        self.assertEqual(self.got["typed"], [])

    def test_ping_needs_the_token(self):
        self.assertEqual(self.req("GET", "/v1/ping")[0], 200)
        self.assertEqual(self.req("GET", "/v1/ping", auth=False)[0], 401)

    def test_foreign_host_and_any_origin_are_refused_before_the_token_is_looked_at(self):
        self.assertEqual(self.req(body={"text": "hi"}, host="evil.example:8775")[0], 403)
        self.assertEqual(self.req(body={"text": "hi"}, host="127.0.0.1:9999")[0], 403)
        self.assertEqual(
            self.req(body={"text": "hi"}, headers={"Origin": "http://localhost:3000"})[0], 403
        )
        self.assertEqual(self.req(body={"text": "hi"}, headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.got["typed"], [])

    def test_localhost_name_is_accepted(self):
        self.assertEqual(self.req(body={"text": "hi"}, host=f"localhost:{self.ch.port}")[0], 200)

    def test_slash_commands_cannot_be_sent(self):
        for t in ("/quit", "/stop", "/audio /etc/passwd", "  /memories"):
            self.assertEqual(self.req(body={"text": t})[0], 400, t)
        self.assertEqual(self.got["typed"], [])

    def test_text_limits_and_types(self):
        self.assertEqual(self.req(body={"text": "x" * 2001})[0], 413)
        self.assertEqual(self.req(body={"text": "x" * 2000})[0], 200)
        for bad in ({"text": ""}, {"text": "   "}, {"text": 5}, {"text": None}, {}, {"txt": "hi"}):
            self.assertEqual(self.req(body=bad)[0], 400, bad)
        self.assertEqual(self.req(body={"text": "a\x07b"})[0], 400)

    def test_body_size_json_and_content_type(self):
        self.assertEqual(self.req(raw=b"x" * (uc.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.req(raw=b"{not json")[0], 400)
        self.assertEqual(self.req(raw=b"[1,2]")[0], 400)
        self.assertEqual(
            self.req(body={"text": "hi"}, headers={"Content-Type": "text/plain"})[0], 415
        )
        self.assertEqual(
            self.req(
                body={"text": "hi"}, headers={"Content-Type": "application/json; charset=utf-8"}
            )[0],
            200,
        )

    def test_paths_limit_and_shape(self):
        ok = [f"/Users/x/Documents/{i}.txt" for i in range(50)]
        self.assertEqual(self.req(path="/v1/drop", body={"paths": ok})[0], 200)
        self.assertEqual(self.got["drop"][0], ok)
        self.assertEqual(self.req(path="/v1/drop", body={"paths": ok + ["/a"]})[0], 413)
        for bad in (
            {"paths": []},
            {"paths": "/a"},
            {"paths": ["rel/path"]},
            {"paths": [1]},
            {"paths": ["/a\x00b"]},
            {"paths": [{"p": "/a"}]},
            {"text": "/a"},
        ):
            self.assertEqual(self.req(path="/v1/drop", body=bad)[0], 400, bad)

    def test_unknown_endpoint_and_method(self):
        self.assertEqual(self.req("POST", "/v1/shell", body={"cmd": "ls"})[0], 404)
        self.assertEqual(self.req("GET", "/v1/typed")[0], 404)
        self.assertEqual(self.req("PUT", "/v1/typed", body={"text": "hi"})[0], 404)

    def test_bad_tokens_have_their_own_rate_limit(self):
        ch = uc.Channel(self.token, {}, audit_path=self.dir / "a2.log", port=0)
        ch.fail_limiter = uc._Limiter(3, 60)
        ch.start()
        try:

            def ping(token):
                c = http.client.HTTPConnection("127.0.0.1", ch.port, timeout=5)
                c.request(
                    "GET",
                    "/v1/ping",
                    headers={"Host": f"127.0.0.1:{ch.port}", "Authorization": "Bearer " + token},
                )
                r = c.getresponse()
                r.read()
                c.close()
                return r.status

            self.assertEqual([ping("wrong") for _ in range(5)], [401, 401, 401, 429, 429])
            self.assertEqual(ping(self.token), 200)  # guessing never locks the app out
        finally:
            ch.stop()

    def test_missing_handler_is_503_and_handler_crash_is_500_without_details(self):
        ch = uc.Channel(
            self.token, {"typed": lambda t: 1 / 0}, audit_path=self.dir / "a3.log", port=0
        ).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", ch.port, timeout=5)
            c.request(
                "POST",
                "/v1/typed",
                body=b'{"text":"hi"}',
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Content-Type": "application/json",
                },
            )
            r = c.getresponse()
            b = r.read().decode()
            self.assertEqual(r.status, 500)
            self.assertNotIn("Zero", b)
            c = http.client.HTTPConnection("127.0.0.1", ch.port, timeout=5)
            c.request(
                "POST",
                "/v1/drop",
                body=b'{"paths":["/a"]}',
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Content-Type": "application/json",
                },
            )
            self.assertEqual(c.getresponse().status, 503)
        finally:
            ch.stop()

    def test_audit_log_records_requests_but_never_the_token_or_the_text(self):
        self.req(body={"text": "my secret sentence"})
        self.req(body={"text": "hi"}, auth=False)
        log = (self.dir / "audit.log").read_text()
        rows = [json.loads(line) for line in log.splitlines()]
        self.assertEqual([r["status"] for r in rows], [200, 401])
        self.assertEqual(rows[0]["chars"], 18)
        self.assertNotIn("secret", log)
        self.assertNotIn(self.token, log)
        self.assertEqual(stat.S_IMODE((self.dir / "audit.log").stat().st_mode), 0o600)

    def test_disabled_by_config_and_port_in_use_do_not_raise(self):
        self.assertIsNone(uc.start_from_config({"enabled": False}, {}, token_path=self.dir / "t2"))
        self.assertIsNone(
            uc.start_from_config(
                {"enabled": True, "port": self.ch.port},
                {},
                token_path=self.dir / "t3",
                audit_path=self.dir / "a4",
            )
        )


class ConfigAndWiring(unittest.TestCase):
    def test_defaults_and_validation(self):
        from julius import config

        c = config.load_config(Path(tempfile.mkdtemp()) / "none.toml")["ui"]
        self.assertEqual((c["enabled"], c["port"], c["typed_reply"]), (True, 8775, "both"))
        p = Path(tempfile.mkdtemp()) / "j.toml"
        p.write_text('[ui]\ntyped_reply = "loud"\n')
        with self.assertRaises(ValueError):
            config.load_config(p)

    def test_candidate_wires_typing_through_the_keyboard_queue_and_text_only_flag(self):
        import inspect

        from julius.runtime import inputs, listen, speech
        from tests.harness import make_session

        self.assertIn("uichannel.start_from_config", inspect.getsource(inputs))
        s = make_session()
        out = inputs.ui_typed(s, "hello")
        self.assertTrue(out["queued"])
        self.assertRegex(out["id"], r"^[0-9a-f]{8}$")
        self.assertIsInstance(s.typed[0], uc.TypedText)
        self.assertIn('"typed_reply") == "text"', inspect.getsource(listen))
        # With text_only set the reply is shown, never synthesized.
        s.text_only = True
        speech.speak_sentence(s, "Hello there.")
        s.tts.synthesize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
