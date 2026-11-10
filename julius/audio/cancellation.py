"""Cooperative cancellation of generation/playback; completed actions stay completed."""

from contextlib import contextmanager
from threading import Event


class TurnCancelled(Exception):
    pass


def check_cancel(event):
    if event is not None and event.is_set():
        raise TurnCancelled("Response stopped by user")


class Cancellation:
    def __init__(self):
        self.event = Event()
        self.busy = Event()
        self.depth = 0

    def check(self):
        check_cancel(self.event)

    def request(self):
        if not self.busy.is_set():
            return False
        self.event.set()
        return True

    @contextmanager
    def activity(self):
        if self.depth == 0:
            self.event.clear()
            self.busy.set()
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            if self.depth == 0:
                self.busy.clear()
                self.event.clear()
