"""A cap on open connections and a deadline for each, shared by the three local servers (8765, 8775, 8785).

Without it, any program on the Mac could open thousands of idle or slowly dripping connections, and each server would
start one thread per connection and keep it (security plan part 4; reproduced by tests/test_port_limits.py). With it:
- at most MAX_CONNS connections are being served at once; the rest are closed straight away;
- a connection still open after its deadline is shut down by one reaper thread, so a flood clears by itself and the
  orb app gets back in.
A long-lived connection that has passed its checks (an authenticated event-feed client) is released from the guard; the
event feed caps those separately (MAX_CLIENTS).
"""

import contextlib
import socket
import threading
import time

MAX_CONNS = 32


class ConnectionGuard:
    def __init__(self, max_conns: int = MAX_CONNS, deadline_s: float = 10.0) -> None:
        self.max_conns, self.deadline_s = max_conns, deadline_s
        self._open: dict[int, tuple[socket.socket, float]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap, daemon=True, name="conn-guard")
        self._reaper.start()

    def admit(self, sock: socket.socket) -> bool:
        """True when the connection may be served (the caller must release it); False means close it now."""
        with self._lock:
            if len(self._open) >= self.max_conns:
                return False
            self._open[id(sock)] = (sock, time.monotonic())
            return True

    def release(self, sock: socket.socket) -> None:
        with self._lock:
            self._open.pop(id(sock), None)

    def count(self) -> int:
        with self._lock:
            return len(self._open)

    def stop(self) -> None:
        self._stop.set()

    def _reap(self) -> None:
        while not self._stop.wait(min(0.25, self.deadline_s / 4)):
            now = time.monotonic()
            with self._lock:
                late = [k for k, (_, t) in self._open.items() if now - t > self.deadline_s]
                socks = [self._open.pop(k)[0] for k in late]
            for s in socks:
                with contextlib.suppress(OSError):
                    s.shutdown(socket.SHUT_RDWR)


class GuardedServerMixin:
    """For socketserver.ThreadingMixIn servers: admit before starting a thread, release when the thread ends."""

    max_conns = MAX_CONNS
    deadline_s = 10.0
    _guard = None

    def guard(self) -> ConnectionGuard:
        if self._guard is None:
            self._guard = ConnectionGuard(self.max_conns, self.deadline_s)
        return self._guard

    def process_request(self, request, client_address):  # type: ignore[no-untyped-def]
        g = self.guard()
        g.deadline_s = self.deadline_s
        if not g.admit(request):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)  # type: ignore[misc]
        except Exception:
            g.release(request)
            raise

    def process_request_thread(self, request, client_address):  # type: ignore[no-untyped-def]
        try:
            super().process_request_thread(request, client_address)  # type: ignore[misc]
        finally:
            self.guard().release(request)

    def server_close(self) -> None:
        super().server_close()  # type: ignore[misc]
        if self._guard is not None:
            self._guard.stop()
