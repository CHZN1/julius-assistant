"""Tiny test client for the UI event feed: prints every event Julius broadcasts.

python -m julius.ui_bridge.events_client            connect to ws://127.0.0.1:8765 and print events (Ctrl+C to stop)
python -m julius.ui_bridge.events_client 8766       another port
python -m julius.ui_bridge.events_client --levels   also print every level event (otherwise a level bar is drawn on one line)"""

import base64
import json
import os
import socket
import struct
import sys


def connect(port=8765, host="127.0.0.1"):
    s = socket.create_connection((host, port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(
        f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(1024)
        if not chunk:
            raise ConnectionError("closed during handshake")
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    if b" 101 " not in head.split(b"\r\n")[0]:
        raise ConnectionError(head.split(b"\r\n")[0].decode())
    s.settimeout(None)
    return s, rest


def events(sock, rest=b""):
    """Yield decoded JSON events until the server closes."""
    buf = rest

    def need(n):
        nonlocal buf
        while len(buf) < n:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        out, buf = buf[:n], buf[n:]
        return out

    while True:
        try:
            b1, b2 = need(2)
            op = b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", need(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", need(8))[0]
            payload = need(ln)
        except ConnectionError:
            return
        if op == 0x8:
            return
        if op == 0x1:
            yield json.loads(payload.decode())


def main(argv):
    port = next((int(a) for a in argv if a.isdigit()), 8765)
    every_level = "--levels" in argv
    sock, rest = connect(port)
    try:
        for ev in events(sock, rest):
            t = ev["type"]
            if t == "level" and not every_level:
                bar = "#" * int(ev["level"] * 40)
                print(f"\r  level {ev['level']:.2f} {bar:<40}", end="", flush=True)
                continue
            if t == "level":
                print(f"level {ev['level']:.3f}")
                continue
            print(
                ("\n" if t != "level" else "")
                + (
                    f"hello: state={ev['state']}, {len(ev['transcript'])} recent lines"
                    if t == "hello"
                    else f"state: {ev['prev']} -> {ev['state']}"
                    if t == "state"
                    else f"{ev['who']:>6}: {ev['text']}"
                    if t == "transcript"
                    else json.dumps(ev)
                ),
                flush=True,
            )
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
