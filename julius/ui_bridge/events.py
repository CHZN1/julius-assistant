"""UI event feed: a read-only WebSocket on 127.0.0.1 that broadcasts what Julius is doing, for a future UI. No UI yet; `julius_events_client.py` prints the events.

Messages (JSON text frames, server -> client only; anything a client sends is ignored apart from ping/close):
  {"type":"hello","state":"idle","level":0.0,"transcript":[...last lines...],"t":...}     on connect
  {"type":"state","state":"idle|listening|thinking|speaking|asleep|quiet","prev":"...","t":...}  on every change
  {"type":"level","level":0.0-1.0,"t":...}        Julius's own voice as it leaves the speakers (RMS x4, clipped), about 30 times a second while he is speaking, then one 0
  {"type":"transcript","who":"you|julius","text":"...","turn":N,"t":...}   a line the user said, or a sentence Julius is saying

Safety: binds 127.0.0.1 only (the host is not configurable); a request whose Host or Origin is not localhost/127.0.0.1 is refused (DNS rebinding, other web pages);
clients cannot send commands; frames from clients larger than 64 kB close the connection; a client that falls 300 messages behind is dropped; at most 8 clients.
No third-party packages: the WebSocket framing (RFC 6455) is implemented here."""

import base64
import collections
import contextlib
import hashlib
import json
import queue
import socket
import struct
import threading
import time
from collections.abc import Callable

from numpy import ndarray

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HOST = "127.0.0.1"
STATES = (
    "idle",
    "listening",
    "thinking",
    "speaking",
    "asleep",
    "quiet",
)  # asleep: ordinary sleep, wakes on "Julius"; quiet: mic off until the wake hotkey
MAX_CLIENTS = 8
LEVEL_GAIN = 4.0


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


def frame(payload: str, opcode: int = 0x1) -> bytes:
    b = payload.encode() if isinstance(payload, str) else payload
    n = len(b)
    head = bytes([0x80 | opcode]) + (
        bytes([n])
        if n < 126
        else b"\x7e" + struct.pack(">H", n)
        if n < 65536
        else b"\x7f" + struct.pack(">Q", n)
    )
    return head + b


def _local_host(value):
    h = (value or "").strip().lower()
    h = (h[1 : h.find("]")] if "]" in h else h) if h.startswith("[") else h.split(":")[0]
    return h in ("127.0.0.1", "localhost", "::1")


def origin_ok(origin):
    if not origin:
        return True  # non-browser clients (the test client) send none
    rest = origin.split("://", 1)[-1]
    return origin.split("://", 1)[0] in ("http", "https") and _local_host(rest.split("/")[0])


class EventHub:
    def __init__(self, transcript_lines: int = 20, clock: Callable = time.time) -> None:
        self.clock = clock
        self.state = "idle"
        self.level = 0.0
        self.transcript = collections.deque(maxlen=transcript_lines)
        self._clients = set()
        self._lock = threading.Lock()

    # -- publishing (called from Julius; never raises, never blocks)
    def _send(self, msg: dict[str, str | float | int]) -> None:
        data = frame(json.dumps(msg, separators=(",", ":")))
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            c.offer(data)

    def set_state(self, state: str) -> None:
        if state not in STATES or state == self.state:
            return
        prev, self.state = self.state, state
        self._send({"type": "state", "state": state, "prev": prev, "t": round(self.clock(), 3)})

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, float(level) * LEVEL_GAIN))
        was = self.level
        self.level = level
        if level > 0.0005 or was > 0.0005:
            self._send({"type": "level", "level": round(level, 3), "t": round(self.clock(), 3)})

    def say(self, who: str, text: str, turn: int | None = None) -> None:
        text = (text or "").strip()
        if not text:
            return
        line = {
            "type": "transcript",
            "who": who,
            "text": text,
            "turn": turn,
            "t": round(self.clock(), 3),
        }
        self.transcript.append(line)
        self._send(line)

    def hello(self) -> dict[str, str | float]:
        return {
            "type": "hello",
            "state": self.state,
            "level": round(self.level, 3),
            "transcript": list(self.transcript),
            "t": round(self.clock(), 3),
        }

    # -- clients
    def add(self, client) -> bool:
        with self._lock:
            if len(self._clients) >= MAX_CLIENTS:
                return False
            self._clients.add(client)
            return True

    def remove(self, client) -> None:
        with self._lock:
            self._clients.discard(client)

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)


class _Client(threading.Thread):
    def __init__(self, sock, hub) -> None:
        super().__init__(daemon=True)
        self.sock, self.hub = sock, hub
        self.q = queue.Queue(maxsize=300)
        self.alive = True

    def offer(self, data: bytes) -> None:
        try:
            self.q.put_nowait(data)
        except queue.Full:
            self.close()  # too slow to keep up

    def close(self) -> None:
        self.alive = False
        with contextlib.suppress(queue.Full):
            self.q.put_nowait(None)
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def _reader(self) -> None:  # client frames are never acted on: only ping and close are answered
        try:
            while self.alive:
                b1, b2 = self._read_exact(2)
                op = b1 & 0x0F
                ln = b2 & 0x7F
                if ln == 126:
                    ln = struct.unpack(">H", self._read_exact(2))[0]
                elif ln == 127:
                    ln = struct.unpack(">Q", self._read_exact(8))[0]
                if ln > 65536:
                    break
                mask = self._read_exact(4) if b2 & 0x80 else b""
                payload = self._read_exact(ln)
                if mask:
                    payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
                if op == 0x8:
                    break
                if op == 0x9:
                    self.q.put_nowait(frame(payload, 0xA))
        except (OSError, ConnectionError, queue.Full):
            pass
        finally:
            self.close()

    def run(self) -> None:
        try:
            self.sock.settimeout(5.0)
            req = b""
            while b"\r\n\r\n" not in req and len(req) < 8192:
                chunk = self.sock.recv(2048)
                if not chunk:
                    return
                req += chunk
            lines = req.decode("latin-1").split("\r\n")
            first = lines[0].split(" ")
            hdr = {
                k.strip().lower(): v.strip()
                for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)
            }
            if (
                len(first) < 2
                or first[0] != "GET"
                or hdr.get("upgrade", "").lower() != "websocket"
                or "sec-websocket-key" not in hdr
            ):
                self.sock.sendall(
                    b"HTTP/1.1 426 Upgrade Required\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                return
            if not _local_host(hdr.get("host", "")) or not origin_ok(hdr.get("origin", "")):
                self.sock.sendall(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                return
            if not self.hub.add(self):
                self.sock.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
                return
            self.sock.sendall(
                (
                    "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "
                    + accept_key(hdr["sec-websocket-key"])
                    + "\r\n\r\n"
                ).encode()
            )
            self.sock.settimeout(None)
            self.q.put_nowait(frame(json.dumps(self.hub.hello(), separators=(",", ":"))))
            threading.Thread(target=self._reader, daemon=True).start()
            while self.alive:
                data = self.q.get()
                if data is None:
                    break
                self.sock.sendall(data)
        except (OSError, ConnectionError, ValueError):
            pass
        finally:
            self.alive = False
            self.hub.remove(self)
            with contextlib.suppress(OSError):
                self.sock.close()


class EventServer:
    def __init__(
        self,
        hub: EventHub,
        port: int = 8765,
        state_fn: Callable | None = None,
        level_fn: Callable | None = None,
        hz: int = 30,
    ) -> None:
        self.hub, self.port, self.state_fn, self.level_fn, self.hz = (
            hub,
            port,
            state_fn,
            level_fn,
            hz,
        )
        self._stop = threading.Event()
        self._sock = None

    def start(self) -> "EventServer":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST, self.port))
        s.listen(8)
        self._sock = s
        self.port = s.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()
        threading.Thread(target=self._sample, daemon=True).start()
        return self

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            _Client(conn, self.hub).start()

    def _sample(self) -> None:
        period = 1.0 / self.hz
        while not self._stop.is_set():
            if self.hub.clients or self.state_fn:
                try:
                    if self.state_fn:
                        self.hub.set_state(self.state_fn())
                    if self.level_fn and self.hub.clients:
                        self.hub.set_level(self.level_fn())
                except Exception:
                    pass
            time.sleep(period if self.hub.clients else 0.25)

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError, AttributeError):
            self._sock.close()


class LevelTap:
    """SpeechPlayer.tap-compatible meter for when duplex audio is off: remembers the loudness of the audio just handed to the player, decaying with time."""

    def __init__(self, clock: Callable = time.monotonic) -> None:
        self.clock = clock
        self._rms = 0.0
        self._at = 0.0

    def out(self, audio: ndarray, rate: int) -> None:
        import numpy as np

        a = np.asarray(audio, dtype=np.float32)
        if a.size:
            self._rms = float(np.sqrt(np.mean(a * a)))
            self._at = self.clock()

    def level(self) -> float:
        return self._rms if self.clock() - self._at < 0.15 else 0.0


def start_from_config(cfg: dict[str, bool | int], state_fn: None = None, level_fn: None = None):
    """cfg is the [events] table. Returns (hub, server) or (None, None) when disabled or the port is taken (Julius never fails to start over a UI feed)."""
    if not (cfg or {}).get("enabled", True):
        return None, None
    hub = EventHub()
    try:
        server = EventServer(hub, int((cfg or {}).get("port", 8765)), state_fn, level_fn).start()
    except OSError:
        return None, None
    return hub, server
