"""DNS rebinding and CSRF against the three local servers, from a real headless Chrome (security plan part 4).

Opt-in, because it needs Google Chrome: JULIUS_TEST_BROWSER=1 python -m unittest tests.test_browser_rebinding

Chrome's --host-resolver-rules maps the attacker names to 127.0.0.1, and its own throwaway profile is used.
Two attacks per server (UI channel, event feed, panel files), each on test copies of the servers on free ports:

- **rebind:** the attack page is served from http://attacker.test:X/ by an "attacker" server. Once the page is loaded
  that server stops and the Julius server starts on the same port X, exactly what a DNS flip to 127.0.0.1 looks like
  to the browser: later requests are same-origin, so a GET carries no Origin header and only the Host check
  (attacker.test) stands in the way. The page is given the real token, as if it had leaked, so the Host check alone is
  what's tested.
- **cross:** a page on another origin (http://attacker.test:Y/) attacks http://127.0.0.1:X/ directly: fetch, a
  no-cors POST, a classic form POST (no custom headers), an image load and a WebSocket. It knows the UI token (as if
  leaked) but not the panel's per-run token.

Pass: the page reads nothing, no UI-channel handler runs, no feed client connects, the panel serves no picture to the
attacker, and the UI channel's audit log records the refusals.
"""

import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import julius.ui_bridge.events as ev
import julius.ui_bridge.panelfiles as pf
import julius.ui_bridge.uichannel as uc

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ENABLED = os.environ.get("JULIUS_TEST_BROWSER") == "1" and os.path.exists(CHROME)

PAGE = """<!doctype html><meta charset=utf-8><title>attack</title>
<iframe name=sink style="display:none"></iframe>
<script>
const P = new URLSearchParams(location.search);
const target = P.get("target"), mode = P.get("mode"), report = P.get("report");
const base = mode === "rebind" ? location.origin : "http://127.0.0.1:" + P.get("port");
const token = P.get("token"), ptoken = P.get("ptoken"), pic = P.get("pic");
const out = {target, mode, base, results: {}};
const wait = ms => new Promise(r => setTimeout(r, ms));
async function attempt(name, fn) {
  try { out.results[name] = await Promise.race([fn(), wait(4000).then(() => "timeout")]); }
  catch (e) { out.results[name] = "error:" + e.name; }
}
async function readable(resp) { return "status:" + resp.status + " body:" + (await resp.text()).slice(0, 80); }
async function run() {
  if (mode === "rebind") await wait(Number(P.get("delay")));
  if (target === "ui") {
    await attempt("get_ping", async () => readable(await fetch(base + "/v1/ping")));
    await attempt("get_ping_with_token", async () => readable(await fetch(base + "/v1/ping",
      {headers: {Authorization: "Bearer " + token}})));
    await attempt("post_typed_json", async () => readable(await fetch(base + "/v1/typed", {method: "POST",
      headers: {Authorization: "Bearer " + token, "Content-Type": "application/json"},
      body: JSON.stringify({text: "rebind says hi"})})));
    await attempt("post_typed_nocors", async () => { const r = await fetch(base + "/v1/typed", {method: "POST",
      mode: "no-cors", headers: {"Content-Type": "text/plain"}, body: '{"text":"csrf says hi"}'});
      return "opaque:" + r.type; });
    await attempt("form_post", async () => {
      const f = document.createElement("form"); f.method = "POST"; f.action = base + "/v1/typed";
      f.enctype = "text/plain"; f.target = "sink";
      const i = document.createElement("input"); i.name = '{"text":"form says hi","x":"'; i.value = '"}';
      f.appendChild(i); document.body.appendChild(f); f.submit(); await wait(1500); return "submitted"; });
  } else if (target === "feed") {
    const wsbase = base.replace("http", "ws");
    for (const [name, protos] of [["ws_with_token", ["julius.v1", "julius.token." + token]],
                                  ["ws_no_token", ["julius.v1"]]]) {
      await attempt(name, () => new Promise(res => {
        const ws = new WebSocket(wsbase + "/", protos);
        ws.onopen = () => { ws.close(); res("OPENED"); };
        ws.onmessage = m => res("MESSAGE:" + String(m.data).slice(0, 60));
        ws.onerror = () => res("refused");
        ws.onclose = e => res("closed:" + e.code);
      }));
    }
  } else if (target === "panel") {
    const src = base + "/" + ptoken + "/p/" + pic;
    await attempt("img", () => new Promise(res => {
      const im = new Image(); im.onload = () => res("LOADED"); im.onerror = () => res("refused"); im.src = src; }));
    await attempt("fetch", async () => readable(await fetch(src)));
  }
  await fetch("http://127.0.0.1:" + report + "/report", {method: "POST", mode: "no-cors",
    headers: {"Content-Type": "text/plain"}, body: JSON.stringify(out)});
}
run();
</script>"""


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Quiet(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass


def page_server(port, served):
    class H(_Quiet):
        def do_GET(self):
            data = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            served.set()

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    srv.allow_reuse_address = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def report_server(got):
    class H(_Quiet):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            got.append(json.loads(self.rfile.read(n)))
            self.send_response(204)
            self.end_headers()

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@unittest.skipUnless(ENABLED, "set JULIUS_TEST_BROWSER=1 (needs Google Chrome)")
class Rebinding(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="julius_rebind_"))
        self.token = uc.ensure_token(self.dir / "ui_token")
        self.calls = []
        rec = lambda name: lambda *a: self.calls.append(name) or {"ok": True}  # noqa: E731
        self.handlers = {
            n: rec(n) for n in ("typed", "drop", "camera", "conversation", "typed_change")
        }
        self.files = pf.PanelFiles(self.dir / "panel", token="panelTOKEN12345678")
        self.pic = self.files.put(b"\x89PNG\r\n\x1a\n" + os.urandom(32), "png").rsplit("/", 1)[1]
        served = []
        orig = self.files.read_ref

        def read_ref(t, n):
            got = orig(t, n)
            if got is not None:
                served.append(n)
            return got

        self.files.read_ref = read_ref
        self.panel_served = served
        self.reports = []
        self.report = report_server(self.reports)
        self.servers = []
        self.chromes = []

    def tearDown(self):
        for p in self.chromes:
            p.kill()
            p.wait(10)
        for stop in self.servers:
            stop()
        self.report.shutdown()
        shutil.rmtree(self.dir, ignore_errors=True)

    def start_target(self, target, port):
        if target == "ui":
            ch = uc.Channel(self.token, self.handlers, self.dir / "audit.log", port=port).start()
            self.servers.append(ch.stop)
        elif target == "feed":
            self.hub = ev.EventHub()
            srv = ev.EventServer(self.hub, port=port, token=self.token).start()
            self.servers.append(srv.stop)
        else:
            srv = pf.PanelFileServer(self.files, port=port).start()
            self.servers.append(srv.stop)

    def chrome(self, url):
        profile = self.dir / f"chrome{len(self.chromes)}"
        p = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                f"--user-data-dir={profile}",
                "--host-resolver-rules=MAP attacker.test 127.0.0.1",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--remote-debugging-port=0",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.chromes.append(p)

    def wait_report(self, n, timeout=60):
        end = time.time() + timeout
        while len(self.reports) < n and time.time() < end:
            time.sleep(0.2)
        self.assertGreaterEqual(len(self.reports), n, "the attack page never reported back")
        return self.reports[n - 1]

    def query(self, target, mode, port, delay=2500):
        # rebind: the real panel token, so the Host check alone is tested. cross: a page on another origin that doesn't
        # know it (with the token, an <img> may show the picture by design; the token in the path is the panel's lock)
        ptoken = "panelTOKEN12345678" if mode == "rebind" else "guessedTOKEN123456"
        return (
            f"target={target}&mode={mode}&port={port}&report={self.report.server_address[1]}"
            f"&token={self.token}&ptoken={ptoken}&pic={self.pic}&delay={delay}"
        )

    def attack(self, target, mode):
        port = free_port()
        if mode == "rebind":
            served = threading.Event()
            attacker = page_server(port, served)
            self.chrome(f"http://attacker.test:{port}/a.html?{self.query(target, mode, port)}")
            self.assertTrue(served.wait(30), "Chrome never loaded the attack page")
            attacker.shutdown()
            attacker.server_close()
            self.start_target(target, port)  # the "DNS flip": same origin, now Julius
        else:
            self.start_target(target, port)
            served = threading.Event()
            attacker = page_server(free_port(), served)
            self.servers.append(attacker.shutdown)
            self.chrome(
                f"http://attacker.test:{attacker.server_address[1]}/a.html?{self.query(target, mode, port)}"
            )
        return self.wait_report(len(self.reports) + 1)

    def assert_nothing_read(self, report):
        for name, got in report["results"].items():
            self.assertNotIn("status:200", got, (report["mode"], name, got))
            self.assertNotIn("OPENED", got, (report["mode"], name, got))
            self.assertNotIn("MESSAGE", got, (report["mode"], name, got))
            self.assertNotIn("LOADED", got, (report["mode"], name, got))

    def test_ui_channel(self):
        for mode in ("rebind", "cross"):
            report = self.attack("ui", mode)
            self.assert_nothing_read(report)
            if (
                mode == "rebind"
            ):  # same origin: the page can read the refusal, and it is the Host check
                self.assertIn("status:403", report["results"]["get_ping"], report)
                self.assertIn("bad Host", report["results"]["get_ping_with_token"], report)
        time.sleep(1)
        self.assertEqual(self.calls, [], "a UI-channel handler ran for a web page")
        audit = [json.loads(x) for x in (self.dir / "audit.log").read_text().splitlines()]
        whys = {a.get("why") for a in audit}
        self.assertTrue({"bad Host", "Origin not allowed"} <= whys, whys)
        self.assertTrue(all(a["status"] >= 400 for a in audit), audit)

    def test_event_feed(self):
        for mode in ("rebind", "cross"):
            report = self.attack("feed", mode)
            self.assert_nothing_read(report)
            self.assertEqual(self.hub.clients, 0)

    def test_panel_files(self):
        for mode in ("rebind", "cross"):
            report = self.attack("panel", mode)
            self.assert_nothing_read(report)
        self.assertEqual(self.panel_served, [], "the panel looked up a picture for a web page")


if __name__ == "__main__":
    unittest.main()
