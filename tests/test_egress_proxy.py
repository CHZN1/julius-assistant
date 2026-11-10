"""Coding-agent network lockdown: the allowlist proxy, and the real seatbelt sandbox talking to it."""

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import julius.agents.code_agent as ca
import julius.agents.egress_proxy as ep


def connect_via(port, target, timeout=5):
    s = socket.create_connection(("127.0.0.1", port), timeout)
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    data = s.recv(4096)
    return s, data.split(b"\r\n", 1)[0].decode()


class Echo:
    """Stands in for the real API host: a local listener that echoes."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(5)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return

            def go(c=c):
                while d := c.recv(1024):
                    c.sendall(b"echo:" + d)
                c.close()

            threading.Thread(target=go, daemon=True).start()


def public_resolver(host, port, type=0):
    return [(2, 1, 6, "", ("160.79.104.10", port))]


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.echo = Echo()
        self.addCleanup(self.echo.srv.close)
        self.log = Path(tempfile.mkdtemp()) / "egress.log"
        self.addCleanup(shutil.rmtree, self.log.parent, True)
        self.p = ep.EgressProxy(
            log_path=self.log,
            resolve=public_resolver,
            dial=lambda ip, port: socket.create_connection(("127.0.0.1", self.echo.port)),
        ).start()
        self.addCleanup(self.p.stop)

    def test_the_allowed_host_passes_bytes_both_ways(self):
        s, status = connect_via(self.p.port, "api.anthropic.com:443")
        self.assertIn("200", status)
        s.sendall(b"hello")
        self.assertEqual(s.recv(100), b"echo:hello")
        s.close()

    def test_every_other_host_is_blocked_and_never_dialled(self):
        dialled = []
        self.p.dial = lambda ip, port: dialled.append((ip, port))
        for target in (
            "example.com:443",
            "api.anthropic.com:80",
            "api.anthropic.com.evil.test:443",
            "evil.test/api.anthropic.com:443",
            "github.com:443",
            "1.1.1.1:443",
            "127.0.0.1:8775",
            "localhost:22",
            "[::1]:443",
            "api.anthropic.com:8443",
            "pypi.org:443",
            "registry.npmjs.org:443",
        ):
            s, status = connect_via(self.p.port, target)
            self.assertIn("403", status, target)
            s.close()
        self.assertEqual(dialled, [])
        self.assertGreaterEqual(len(self.p.blocked), 12)
        self.assertIn("blocked example.com:443", self.log.read_text())

    def test_plain_http_and_other_verbs_are_refused(self):
        for line in (
            "GET http://example.com/ HTTP/1.1",
            "POST http://api.anthropic.com/v1/messages HTTP/1.1",
            "GET / HTTP/1.1",
        ):
            s = socket.create_connection(("127.0.0.1", self.p.port), 5)
            s.sendall(line.encode() + b"\r\nHost: x\r\n\r\n")
            self.assertIn("405", s.recv(200).decode())
            s.close()

    def test_case_and_trailing_dot_do_not_bypass_but_do_not_break_the_allowed_host(self):
        s, status = connect_via(self.p.port, "API.Anthropic.COM.:443")
        self.assertIn("200", status)
        s.close()  # same host, normalised
        s, status = connect_via(self.p.port, "api.anthropic.com@evil.test:443")
        self.assertNotIn("200", status)
        s.close()

    def test_allowed_name_resolving_to_a_private_address_is_refused(self):
        self.p.resolve = lambda host, port, type=0: [(2, 1, 6, "", ("10.0.0.7", port))]
        s, status = connect_via(self.p.port, "api.anthropic.com:443")
        self.assertIn("403", status)
        s.close()

    def test_garbage_and_oversize_headers_do_not_crash_it(self):
        s = socket.create_connection(("127.0.0.1", self.p.port), 5)
        s.sendall(b"\x00\xff" * 6000)
        s.close()
        s = socket.create_connection(("127.0.0.1", self.p.port), 5)
        s.sendall(b"CONNECT nonsense HTTP/1.1\r\n\r\n")
        self.assertIn("400", s.recv(100).decode())
        s.close()
        s, status = connect_via(self.p.port, "api.anthropic.com:443")
        self.assertIn("200", status)
        s.close()

    def test_env_points_every_proxy_variable_at_it_and_clears_no_proxy(self):
        env = self.p.env()
        url = f"http://127.0.0.1:{self.p.port}"
        for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
            self.assertEqual(env[k], url)
        self.assertEqual(env["NO_PROXY"], "")


class WiringTests(unittest.TestCase):
    def setUp(self):
        from tests.test_code_agent import Base

        self.b = Base()
        self.b.setUp()
        self.addCleanup(self.b.tearDown)

    def test_profile_allows_only_the_proxy_port_and_nothing_else_on_the_network(self):
        t = self.b.task()
        text = ca.seatbelt_profile(t, home=self.b.home, proxy_port=54321)
        net = [line for line in text.splitlines() if "network" in line]
        self.assertEqual(
            net, ["(deny network*)", '(allow network-outbound (remote ip "localhost:54321"))']
        )
        for old in ('"*:443"', "*:53", "mDNSResponder", "localhost:*"):
            self.assertNotIn(old, text)
        self.assertEqual(
            [
                line
                for line in ca.seatbelt_profile(t, home=self.b.home).splitlines()
                if "network" in line
            ],
            ["(deny network*)"],
        )  # no proxy, no network

    def test_command_carries_proxy_env_and_matching_profile(self):
        t = self.b.task()
        p = ep.EgressProxy().start()
        self.addCleanup(p.stop)
        argv, env, _ = ca.build_command(t, "sk-test", config=self.b.cfg, proxy=p)
        self.assertEqual(env["HTTPS_PROXY"], f"http://127.0.0.1:{p.port}")
        self.assertIn(f"localhost:{p.port}", (t.dir / "sandbox.sb").read_text())


@unittest.skipUnless(os.path.exists("/usr/bin/sandbox-exec"), "needs macOS sandbox-exec")
class SandboxEndToEnd(unittest.TestCase):
    """A real sandboxed process: direct connections are impossible, only the proxy port is reachable, and the proxy only lets the API host through."""

    def setUp(self):
        from tests.test_code_agent import Base

        self.b = Base()
        self.b.setUp()
        self.addCleanup(self.b.tearDown)
        self.echo = Echo()
        self.addCleanup(self.echo.srv.close)
        self.p = ep.EgressProxy(
            resolve=public_resolver,
            dial=lambda ip, port: socket.create_connection(("127.0.0.1", self.echo.port)),
        ).start()
        self.addCleanup(self.p.stop)
        self.t = self.b.task()
        (self.t.dir / "p.sb").write_text(
            ca.seatbelt_profile(self.t, home=self.b.home, proxy_port=self.p.port)
        )

    def run_py(self, code):
        return subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-f",
                str(self.t.dir / "p.sb"),
                "/usr/bin/python3",
                "-c",
                code,
            ],
            cwd=self.t.work,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_direct_connections_fail_but_the_proxy_port_works(self):
        r = self.run_py(f"""
import socket
def try_(host, port):
    try: socket.create_connection((host, port), 3).close(); return 'open'
    except OSError as e: return 'blocked'
print('direct_echo', try_('127.0.0.1', {self.echo.port}))
print('direct_443', try_('1.1.1.1', 443))
print('direct_80', try_('1.1.1.1', 80))
print('proxy', try_('127.0.0.1', {self.p.port}))
""")
        out = r.stdout
        self.assertIn("direct_echo blocked", out, r.stderr)
        self.assertIn("direct_443 blocked", out)
        self.assertIn("direct_80 blocked", out)
        self.assertIn("proxy open", out)

    def test_through_the_proxy_only_the_api_host_gets_out(self):
        r = self.run_py(f"""
import socket
def ask(t):
    s = socket.create_connection(('127.0.0.1', {self.p.port}), 5); s.sendall(('CONNECT %s HTTP/1.1\\r\\n\\r\\n' % t).encode()); return s.recv(200).split(b'\\r\\n')[0].decode()
print('api', ask('api.anthropic.com:443')); print('github', ask('github.com:443')); print('pypi', ask('pypi.org:443'))
""")
        self.assertIn("api HTTP/1.1 200", r.stdout, r.stderr)
        self.assertIn("github HTTP/1.1 403", r.stdout)
        self.assertIn("pypi HTTP/1.1 403", r.stdout)


if __name__ == "__main__":
    unittest.main()
