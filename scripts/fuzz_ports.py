"""Fuzz the three local servers (UI channel 8775, event feed 8765, panel files 8785) over real sockets.

    python scripts/fuzz_ports.py --minutes 10            a nightly-sized run
    python scripts/fuzz_ports.py --hours 24 --out DIR    the long run (security plan part 4)
    python scripts/fuzz_ports.py --replay DIR/finding-*.json

Throwaway copies of the servers run in this process on free ports, with their own token and stub handlers, so nothing
reaches the live Julius, no turn runs and no API is called. Standard library only. A mutation fuzzer: valid requests
for every endpoint are the seeds, and each case is a seed with random byte, header, length and JSON changes, or random
WebSocket frames after a valid handshake.

A finding (saved as a replayable JSON file in --out) is any of:
- a 5xx from the UI channel (the stub handlers never fail, so it is the server's own parsing);
- a success without the right token, with an Origin, or with a foreign Host (UI channel 2xx, event feed 101), or the
  panel serving anything other than the one stored picture by its exact path;
- no answer and no close within 20 s (a hang);
- a server that stops answering a known-good request;
- threads above 200, or memory 500 MB above where it started.
Exit status 1 when anything was found.
"""

import argparse
import base64
import json
import os
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JULIUS_STATE_DIR", tempfile.mkdtemp(prefix="julius_fuzz_state_"))

import contextlib

import julius.ui_bridge.events as ev  # noqa: E402
import julius.ui_bridge.panelfiles as pf  # noqa: E402
import julius.ui_bridge.uichannel as uc  # noqa: E402

HANG_S = 20.0
MAX_THREADS = 200
MAX_RSS_GROWTH_MB = 500
INTERESTING = [
    b"",
    b"0",
    b"-1",
    b"99999999999999999999",
    b"4294967296",
    b"1e9",
    b" ",
    b"\x00",
    b"\r\n",
    b"\xff\xfe",
    "é☃\U0001f600".encode(),
    b"%00",
    b"../../etc/passwd",
    b"A" * 70000,
    b"Bearer ",
    b"null",
]


# ---- the servers under test ----
class Targets:
    def __init__(self, root: Path):
        self.token = uc.ensure_token(root / "ui_token")
        ok = lambda *a, **k: {"ok": True}  # noqa: E731
        handlers = {
            "typed": lambda t: {"queued": True},
            "drop": lambda p: {"staged": len(p)},
            "photo": lambda data, auto: {"held": len(data)},
            "camera": lambda e: {},
            "sprite_get": lambda: None,
            "sprite_post": lambda edits, sid: (True, ""),
            "sprite_ops": ok,
            "conversation": ok,
            "typed_change": ok,
        }
        big = uc._Limiter(
            10**9, 1
        )  # the limiter has its own tests; here it would only hide the parser
        self.ui = uc.Channel(self.token, handlers, root / "audit.log", port=0, limiter=big)
        self.ui.fail_limiter = uc._Limiter(10**9, 1)
        self.ui.card_limiter = uc._Limiter(10**9, 1)
        self.ui.start()
        self.hub = ev.EventHub()
        self.feed = ev.EventServer(self.hub, port=0, token=self.token).start()
        self.files = pf.PanelFiles(root / "panel", token="fuzzpaneltoken123")
        self.picture = self.files.put(b"\x89PNG\r\n\x1a\n" + os.urandom(64), "png")
        self.panel = pf.PanelFileServer(self.files, port=0).start()
        self.panel_path = "/fuzzpaneltoken123" + self.picture


# ---- seeds ----
def http_request(method, path, headers, body=b""):
    lines = [f"{method} {path} HTTP/1.1".encode()]
    lines += [k + b": " + v for k, v in headers]
    return b"\r\n".join(lines) + b"\r\n\r\n" + body


def ui_seeds(t):
    host = f"127.0.0.1:{t.ui.port}".encode()
    auth = b"Bearer " + t.token.encode()
    js = lambda o: json.dumps(o).encode()  # noqa: E731
    bodies = [
        ("POST", "/v1/typed", {"text": "what's the weather"}),
        ("POST", "/v1/drop", {"paths": ["/tmp/a.txt", "/tmp/b.png"]}),
        ("POST", "/v1/camera", {"event": "opened"}),
        ("POST", "/v1/typed/edit", {"id": "0123abcd", "text": "hi"}),
        ("POST", "/v1/typed/cancel", {"id": "0123abcd"}),
        ("POST", "/v1/conversation", {"action": "search", "query": "x"}),
        ("POST", "/v1/sprite", {"id": "s1", "edits": {"0": {"1,2": 3}}}),
        ("POST", "/v1/sprite/edit", {"id": "s1", "ops": ["bigger beard"]}),
    ]
    seeds = []
    for method, path, obj in bodies:
        b = js(obj)
        seeds.append(
            http_request(
                method,
                path,
                [
                    (b"Host", host),
                    (b"Authorization", auth),
                    (b"Content-Type", b"application/json"),
                    (b"Content-Length", str(len(b)).encode()),
                ],
                b,
            )
        )
    seeds.append(http_request("GET", "/v1/ping", [(b"Host", host), (b"Authorization", auth)]))
    seeds.append(http_request("GET", "/v1/sprite", [(b"Host", host), (b"Authorization", auth)]))
    jpeg = b"\xff\xd8\xff\xe0" + os.urandom(200)
    seeds.append(
        http_request(
            "POST",
            "/v1/photo?auto=1",
            [
                (b"Host", host),
                (b"Authorization", auth),
                (b"Content-Type", b"image/jpeg"),
                (b"Content-Length", str(len(jpeg)).encode()),
            ],
            jpeg,
        )
    )
    return seeds


def feed_handshake(t, extra=()):
    h = [
        (b"Host", f"127.0.0.1:{t.feed.port}".encode()),
        (b"Upgrade", b"websocket"),
        (b"Connection", b"Upgrade"),
        (b"Sec-WebSocket-Key", base64.b64encode(os.urandom(16))),
        (b"Sec-WebSocket-Version", b"13"),
        (b"Authorization", b"Bearer " + t.token.encode()),
        *extra,
    ]
    return http_request("GET", "/", h)


def panel_seeds(t):
    host = f"127.0.0.1:{t.panel.port}".encode()
    return [
        http_request("GET", t.panel_path, [(b"Host", host), (b"Connection", b"close")]),
        http_request("GET", t.panel_path + "?x=1", [(b"Host", host), (b"Connection", b"close")]),
    ]


# ---- mutation ----
def mutate_json(rng, body):
    try:
        obj = json.loads(body)
    except ValueError:
        return body
    if not isinstance(obj, dict):
        obj = {"value": obj}
    choices = [
        lambda: "[" * rng.randint(1000, 9000) + "]" * rng.randint(0, 9000),
        lambda: json.dumps(
            {k: rng.choice([None, 0, -1, 1e308, "", [], {}, "x" * 3000, True]) for k in obj}
        ),
        lambda: json.dumps(rng.choice([[], "s", 1, None, [obj]])),
        lambda: json.dumps({**obj, rng.choice(list(obj) or ["k"]): {"$": [obj] * 3}}),
        lambda: json.dumps(obj).replace('"', "'", 1),
        lambda: json.dumps(
            {**obj, "text": "".join(chr(rng.randint(0, 0x10FFFF)) for _ in range(50))}
        ),
        lambda: '{"a":' * 500 + "1" + "}" * 500,
        lambda: json.dumps(obj) + "garbage",
        lambda: "NaN",
    ]
    return rng.choice(choices)().encode("utf-8", "surrogatepass")


def mutate(rng, data, seeds):
    head, sep, body = data.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    for _ in range(rng.randint(1, 4)):
        op = rng.randrange(12)
        if op == 0 and data:  # flip bytes
            b = bytearray(head + sep + body)
            for _ in range(rng.randint(1, 8)):
                b[rng.randrange(len(b))] = rng.randrange(256)
            head, sep, body = bytes(b).partition(b"\r\n\r\n")
            lines = head.split(b"\r\n")
        elif op == 1 and len(lines) > 1:  # drop a header
            del lines[rng.randrange(1, len(lines))]
        elif op == 2 and len(lines) > 1:  # duplicate a header
            lines.insert(rng.randrange(1, len(lines) + 1), rng.choice(lines[1:]))
        elif op == 3 and len(lines) > 1:  # an interesting value
            i = rng.randrange(1, len(lines))
            k = lines[i].split(b":", 1)[0]
            lines[i] = k + b": " + rng.choice(INTERESTING)
        elif op == 4:
            lines.append(
                rng.choice(
                    [b"Origin: http://evil.test", b"Origin: null", b"Origin: http://127.0.0.1"]
                )
            )
        elif op == 5:
            lines.append(
                b"Host: "
                + rng.choice([b"evil.test", b"127.0.0.1.evil.test", b"localhost", b"[::1]:1"])
            )
        elif op == 6:  # wrong Content-Length
            lines = [x for x in lines if not x.lower().startswith(b"content-length")]
            lines.append(
                b"Content-Length: " + str(len(body) + rng.choice([-5, -1, 1, 7, 10**6])).encode()
            )
        elif op == 7 and body:
            body = mutate_json(rng, body)
            lines = [x for x in lines if not x.lower().startswith(b"content-length")]
            lines.append(b"Content-Length: " + str(len(body)).encode())
        elif op == 8:  # method or path
            parts = lines[0].split(b" ")
            if len(parts) >= 2:
                if rng.random() < 0.5:
                    parts[0] = rng.choice(
                        [b"GET", b"POST", b"PUT", b"OPTIONS", b"HEAD", b"BREW", b"", b"get"]
                    )
                else:
                    parts[1] = parts[1] + rng.choice(
                        [b"/", b"/..", b"?a=b", b"%2e%2e", b"//", b"#x", b"\x00", b"/p/../" * 5]
                    )
                lines[0] = b" ".join(parts)
        elif op == 9:  # splice with another seed
            other = rng.choice(seeds)
            cut = rng.randrange(len(other) + 1)
            return (b"\r\n".join(lines) + sep + body)[: rng.randrange(len(data) + 1)] + other[cut:]
        elif op == 10:  # truncate
            return (b"\r\n".join(lines) + sep + body)[: rng.randrange(len(data) + 1)]
        else:  # random junk
            return os.urandom(rng.randint(0, 3000))
    return b"\r\n".join(lines) + sep + body


def random_frames(rng):
    out = b""
    for _ in range(rng.randint(1, 6)):
        op = rng.choice([0x0, 0x1, 0x2, 0x8, 0x9, 0xA, 0x3, 0xF])
        fin = rng.choice([0x80, 0x00])
        n = rng.choice([0, 1, 125, 126, 127, 65535, 65536, 70000, rng.randint(0, 300)])
        masked = rng.random() < 0.8
        if n < 126:
            ln = bytes([n | (0x80 if masked else 0)])
        elif n < 65536:
            ln = bytes([126 | (0x80 if masked else 0)]) + n.to_bytes(2, "big")
        else:
            ln = bytes([127 | (0x80 if masked else 0)]) + rng.choice(
                [n, 2**63 - 1, 2**64 - 1]
            ).to_bytes(8, "big")
        payload = os.urandom(min(n, 4000))
        out += bytes([fin | op]) + ln + (os.urandom(4) if masked else b"") + payload
        if rng.random() < 0.2:
            out += os.urandom(rng.randint(1, 20))
    return out


# ---- sending and judging ----
def exchange(port, data, wait_body=True):
    """Send, then read until the response is complete or the server closes. ('hang', b'') after HANG_S."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        s.settimeout(HANG_S)
        with contextlib.suppress(OSError):  # the server may refuse before reading it all
            s.sendall(data)
        got, start = b"", time.monotonic()
        while time.monotonic() - start < HANG_S:
            try:
                chunk = s.recv(65536)
            except TimeoutError:
                return "hang", got
            except OSError:
                return "closed", got
            if not chunk:
                return "closed", got
            got += chunk
            head, sep, rest = got.partition(b"\r\n\r\n")
            if sep:
                if head.startswith(b"HTTP/1.1 101") or not wait_body:
                    return "done", got
                cl = [x for x in head.split(b"\r\n") if x.lower().startswith(b"content-length:")]
                if cl and len(rest) >= int(cl[0].split(b":")[1]):
                    return "done", got
        return "hang", got
    finally:
        s.close()


def status_of(resp):
    try:
        return int(resp.split(b" ", 2)[1])
    except (IndexError, ValueError):
        return None


def headers_of(data):
    head = [
        x.rstrip(b"\r") for x in data.partition(b"\r\n\r\n")[0].split(b"\n")
    ]  # as http.client reads it
    out = {}
    for line in head[1:]:
        k, _, v = line.partition(b":")
        out.setdefault(k.strip().lower(), []).append(v.strip())
    return head[0], out


def ui_should_succeed(t, data):
    first, h = headers_of(data)
    hosts = h.get(b"host", [])
    good_host = bool(hosts) and hosts[0].lower() in (
        f"127.0.0.1:{t.ui.port}".encode(),
        f"localhost:{t.ui.port}".encode(),
    )
    auth = h.get(b"authorization", [b""])[0]
    good_token = auth.startswith(b"Bearer ") and auth[7:].strip() == t.token.encode()
    return good_host and good_token and b"origin" not in h


class Fuzzer:
    def __init__(self, targets, out: Path, seed: int):
        self.t, self.out, self.rng = targets, out, random.Random(seed)
        self.findings, self.cases = 0, 0
        self.lock = threading.Lock()

    def found(self, kind, server, data, detail=""):
        with self.lock:
            self.findings += 1
            path = self.out / f"finding-{self.findings:04d}-{server}-{kind}.json"
        path.write_text(
            json.dumps(
                {
                    "kind": kind,
                    "server": server,
                    "detail": detail,
                    "data": base64.b64encode(data).decode(),
                },
                indent=1,
            )
        )
        print(f"FINDING {kind} on {server}: {detail} -> {path}", flush=True)

    def ui_case(self, rng, seeds):
        data = mutate(rng, rng.choice(seeds), seeds)
        outcome, resp = exchange(self.t.ui.port, data)
        code = status_of(resp)
        if outcome == "hang":
            self.found("hang", "ui", data)
        elif (
            code is not None and code >= 500 and code not in (501, 503)
        ):  # 501: the stdlib's answer to an unknown method
            self.found("5xx", "ui", data, resp[:300].decode("latin-1"))
        elif code is not None and 200 <= code < 300 and not ui_should_succeed(self.t, data):
            self.found("auth-bypass", "ui", data, resp[:300].decode("latin-1"))

    def feed_case(self, rng):
        kind = rng.random()
        if kind < 0.4:  # a mutated handshake
            base = feed_handshake(self.t)
            data = mutate(rng, base, [base])
            outcome, resp = exchange(self.t.feed.port, data, wait_body=False)
            if outcome == "hang":
                self.found("hang", "feed", data)
            elif resp.startswith(b"HTTP/1.1 101"):
                _, h = headers_of(data)
                host = (h.get(b"host") or [b""])[0].split(b":")[0].lower()
                origin = (h.get(b"origin") or [b""])[0]
                auths = h.get(b"authorization") or [b""]
                proto = b",".join(h.get(b"sec-websocket-protocol", []))
                tok = self.t.token.encode()
                authed = any(a[7:].strip() == tok for a in auths) or b"julius.token." + tok in proto
                if (
                    host not in (b"127.0.0.1", b"localhost")
                    or not authed
                    or (origin and b"127.0.0.1" not in origin and b"localhost" not in origin)
                ):
                    self.found("auth-bypass", "feed", data, resp[:200].decode("latin-1"))
        else:  # a valid handshake, then random frames
            data = feed_handshake(self.t) + random_frames(rng)
            try:
                s = socket.create_connection(("127.0.0.1", self.t.feed.port), timeout=5)
                s.sendall(data)
                s.settimeout(2)
                with contextlib.suppress(OSError):
                    s.recv(65536)
                s.close()
            except OSError:
                pass

    def panel_case(self, rng, seeds):
        data = mutate(rng, rng.choice(seeds), seeds)
        outcome, resp = exchange(self.t.panel.port, data)
        if outcome == "hang":
            self.found("hang", "panel", data)
            return
        if status_of(resp) == 200:
            first, h = headers_of(data)
            parts = first.split(b" ")
            host = (h.get(b"host") or [b""])[0].rsplit(b":", 1)[0].strip(b"[]").lower()
            if (
                len(parts) < 2
                or parts[1].split(b"#")[0] != self.t.panel_path.encode()  # a #fragment is dropped
                or host not in (b"127.0.0.1", b"localhost", b"::1")
            ):
                self.found("served-other", "panel", data, first.decode("latin-1"))

    def alive(self):
        ui = exchange(self.t.ui.port, ui_seeds(self.t)[8])[1]
        panel = exchange(self.t.panel.port, panel_seeds(self.t)[0])[1]
        feed = exchange(self.t.feed.port, feed_handshake(self.t), wait_body=False)[1]
        bad = [
            n
            for n, r, want in (("ui", ui, 200), ("panel", panel, 200), ("feed", feed, 101))
            if status_of(r) != want
        ]
        for n in bad:
            self.found("down", n, b"", "a known-good request failed")
        return not bad


def rss_mb():
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True
    ).stdout
    return int(out.strip() or 0) / 1024


def run(args):
    out = Path(args.out or tempfile.mkdtemp(prefix="julius_fuzz_"))
    out.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="julius_fuzz_servers_"))
    t = Targets(root)
    seed = args.seed if args.seed is not None else random.randrange(2**32)
    fz = Fuzzer(t, out, seed)
    print(f"fuzzing for {args.seconds:.0f} s, seed {seed}, findings in {out}", flush=True)
    useeds, pseeds = ui_seeds(t), panel_seeds(t)
    stop = time.monotonic() + args.seconds

    def worker(name, fn, n):
        rng = random.Random(f"{seed}-{name}")
        while time.monotonic() < stop:
            try:
                fn(rng)
            except Exception as e:  # the fuzzer's own bug: report, keep going
                fz.found("harness-error", name, b"", repr(e))
            with fz.lock:
                fz.cases += 1

    jobs = {
        "ui": lambda r: fz.ui_case(r, useeds),
        "feed": fz.feed_case,
        "panel": lambda r: fz.panel_case(r, pseeds),
    }
    workers = [
        threading.Thread(target=worker, args=(f"{name}{i}", fn, 0), daemon=True)
        for name, fn in jobs.items()
        for i in range(args.workers)
    ]
    base_rss, peak_threads = rss_mb(), 0
    for w in workers:
        w.start()
    last = 0.0
    while time.monotonic() < stop:
        time.sleep(1)
        peak_threads = max(peak_threads, threading.active_count())
        if threading.active_count() > MAX_THREADS:
            fz.found("threads", "all", b"", f"{threading.active_count()} threads")
        if time.monotonic() - last >= args.check_s:
            last = time.monotonic()
            fz.alive()
            rss = rss_mb()
            if rss - base_rss > MAX_RSS_GROWTH_MB:
                fz.found("memory", "all", b"", f"rss {rss:.0f} MB, started at {base_rss:.0f} MB")
            print(
                f"{time.strftime('%H:%M:%S')} cases={fz.cases} findings={fz.findings} threads={threading.active_count()} rss={rss:.0f}MB",
                flush=True,
            )
    for w in workers:
        w.join(HANG_S + 5)
    fz.alive()
    summary = {
        "seed": seed,
        "seconds": args.seconds,
        "cases": fz.cases,
        "findings": fz.findings,
        "peak_threads": peak_threads,
        "rss_start_mb": round(base_rss),
        "rss_end_mb": round(rss_mb()),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary), flush=True)
    return 1 if fz.findings else 0


def replay(paths):
    root = Path(tempfile.mkdtemp(prefix="julius_fuzz_replay_"))
    t = Targets(root)
    for p in paths:
        f = json.loads(Path(p).read_text())
        port = {"ui": t.ui.port, "feed": t.feed.port, "panel": t.panel.port}.get(f["server"])
        if port is None:
            continue
        outcome, resp = exchange(port, base64.b64decode(f["data"]), wait_body=f["server"] != "feed")
        print(p, f["kind"], "->", outcome, resp[:120])
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--minutes", type=float)
    ap.add_argument("--hours", type=float)
    ap.add_argument("--out")
    ap.add_argument("--seed", type=int)
    ap.add_argument(
        "--check-s", type=float, default=60.0, help="seconds between liveness and memory checks"
    )
    ap.add_argument("--workers", type=int, default=4, help="workers per server")
    ap.add_argument("--replay", nargs="+")
    args = ap.parse_args()
    if args.replay:
        return replay(args.replay)
    args.seconds = (args.hours or 0) * 3600 + (args.minutes or 0) * 60 or 600
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
