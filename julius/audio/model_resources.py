"""Exclusive on-demand model jobs. Caller registers every resident model.

This is a handoff foundation, not a memory-cap guarantee. Real model jobs must
also run under the existing resource guard until their peak usage is measured.
"""

import re
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import Mock


class ResourceUnavailable(RuntimeError):
    pass


def mac_memory_snapshot():
    pressure = subprocess.check_output(["/usr/bin/memory_pressure", "-Q"], text=True, timeout=5)
    swap = subprocess.check_output(["/usr/sbin/sysctl", "vm.swapusage"], text=True, timeout=5)
    return {
        "free_percent": int(
            re.search(r"System-wide memory free percentage: (\d+)%", pressure).group(1)
        ),
        "swap_mb": float(re.search(r"used = ([\d.]+)M", swap).group(1)),
    }


class ModelResources:
    def __init__(
        self, snapshot: Mock = mac_memory_snapshot, minimum_free_percent: int = 30
    ) -> None:
        self.snapshot = snapshot
        self.minimum_free_percent = minimum_free_percent
        self._lock = threading.Lock()
        self._releases = {}
        self.active = None

    def register(self, name: str, release: Mock) -> None:
        if not callable(release):
            raise TypeError("A model needs an unload callback")
        if not self._lock.acquire(blocking=False):
            raise ResourceUnavailable("A model job is active")
        try:
            if name in self._releases:
                raise ValueError("Model already registered")
            self._releases[name] = release
        finally:
            self._lock.release()

    @contextmanager
    def job(self, name: str) -> Iterator[dict[str, int]]:
        if not self._lock.acquire(blocking=False):
            raise ResourceUnavailable("Another model job is active")
        try:
            if name not in self._releases:
                raise ValueError("Register the model and its unload callback first")
            # Include every known resident model, even if loaded outside this API.
            # An unload failure prevents the requested job from starting.
            for release in self._releases.values():
                release()
            state = self.snapshot()
            if state["free_percent"] < self.minimum_free_percent:
                raise ResourceUnavailable("Not enough memory headroom; model job was not started")
            self.active = name
            try:
                yield state
            finally:
                self._releases[name]()
        finally:
            self.active = None
            self._lock.release()
