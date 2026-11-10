"""Global hotkeys (stop, wake) through Carbon RegisterEventHotKey, which needs no Accessibility or Input Monitoring permission.

A tiny helper (hotkeyd/hotkeyd.c, built on first use into ~/.julius/bin) registers the key and prints "fire <name>" on each press; it exits when this process closes its stdin."""

import contextlib
import os
import queue
import subprocess
import threading
from pathlib import Path

from julius.paths import REPO_ROOT

ROOT = REPO_ROOT
SOURCE = ROOT / "hotkeyd" / "hotkeyd.c"
BINARY = Path(
    os.environ.get("JULIUS_HOTKEYD") or Path.home() / ".julius" / "bin" / "julius-hotkeyd"
)


def build(source=SOURCE, binary=BINARY, runner=subprocess.run):
    """Compile the helper when missing or older than its source. Returns the binary path; raises with the compiler output if it cannot."""
    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    r = runner(
        ["/usr/bin/clang", "-O2", str(source), "-framework", "Carbon", "-o", str(binary)],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not binary.exists():
        raise RuntimeError("could not build the hotkey helper: " + (r.stderr or "")[-300:])
    return binary


class Hotkey:
    def __init__(self, proc):
        self.proc = proc

    def stop(self):
        with contextlib.suppress(Exception):
            self.proc.stdin.close()
        with contextlib.suppress(Exception):
            self.proc.terminate()


def start(
    stop, combo="<ctrl>+<alt>+s", say=print, popen=subprocess.Popen, binary=None, timeout=5.0
):
    """Register `combo` globally; `stop()` is called on a worker thread for each press. Returns a handle with .stop(), or None when it cannot start."""
    try:
        proc = popen(
            [str(binary or build()), f"key={combo}"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        first = queue.Queue()

        def read():
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("fire"):
                    try:
                        stop()
                    except Exception as e:
                        say(f"  [hotkey handler failed: {e}]")
                else:
                    first.put(line)
            first.put("closed")

        threading.Thread(target=read, daemon=True).start()
        failure = None
        while True:
            try:
                line = first.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError("helper did not answer") from None
            if line.startswith("failed"):
                failure = line
            elif line.startswith("ready"):
                if failure or "key" not in line.split():
                    raise RuntimeError(failure or "key not registered")
                break
            elif line == "closed":
                raise RuntimeError(failure or "helper exited")
        say(f"  [hotkey {combo} active]")
        return Hotkey(proc)
    except Exception as e:
        with contextlib.suppress(Exception):
            proc.terminate()
        say(f"  [hotkey {combo} unavailable: {e}]")
        return None
