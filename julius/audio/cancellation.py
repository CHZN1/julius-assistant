"""Cooperative cancellation of generation/playback; completed actions stay completed."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event


class TurnCancelled(Exception):
    pass


def check_cancel(event: Event | None) -> None:
    if event is not None and event.is_set():
        raise TurnCancelled("Response stopped by user")


class Cancellation:
    def __init__(self) -> None:
        self.event = Event()
        self.busy = Event()
        self.depth = 0

    def check(self) -> None:
        check_cancel(self.event)

    def request(self) -> bool:
        if not self.busy.is_set():
            return False
        self.event.set()
        return True

    @contextmanager
    def activity(self) -> Iterator[None]:
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
