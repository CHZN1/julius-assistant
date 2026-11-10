"""Startup smoke check for the always-on service (Phase F4): would have caught the earlier crash loop in seconds instead of hours.

    python -m julius.service.service smoke              check the running service as it is
    python -m julius.service.service smoke --restart    restart it first, then check the fresh start (also catches a traceback written during startup)

Passes only when, after the (re)start: the service is loaded and running; "julius ready" appears in the log within the timeout; both hotkeys report active;
the duplex audio line appears when [audio] duplex is on; the process id and launchd run counter stay unchanged for `stable` seconds after ready (a crash loop changes them);
and launchd's stderr gained no Traceback. Everything it reads is injectable, so the logic is unit-tested without a service."""

import json
import time
from collections.abc import Callable
from typing import Any

READY = "julius ready"
HOTKEYS = ("+s active]", "+j active]")


def lines_since(read_lines: Callable, start_ts: str) -> list[Any | str]:
    out = []
    for raw in read_lines():
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if str(r.get("ts", "")) >= start_ts:
            out.append(str(r.get("msg", "")))
    return out


def check(
    state: Callable,
    read_log: Callable,
    read_err_tail: Callable,
    start_ts: str,
    duplex: bool = True,
    timeout: int = 120.0,
    stable: int = 20.0,
    clock: Any = time.monotonic,
    sleep: Callable = time.sleep,
) -> tuple[Any, ...]:
    """state() -> dict(state, pid, runs); read_log() -> raw jsonl lines; read_err_tail() -> text written to launchd stderr since the restart; start_ts -> ISO timestamp string.
    Returns (ok, [(name, ok, detail)])."""
    results = []

    def add(name, ok, detail=""):
        return results.append((name, bool(ok), detail))

    st = state()
    add(
        "service running",
        st.get("state") == "running" or st.get("pid") not in (None, "", "0"),
        f"state={st.get('state')} pid={st.get('pid')}",
    )
    deadline = clock() + timeout
    msgs = []
    while True:
        msgs = lines_since(read_log, start_ts)
        if any(READY in m for m in msgs) or clock() >= deadline:
            break
        sleep(1.0)
    ready = any(READY in m for m in msgs)
    add("julius ready", ready, "" if ready else f"not seen within {timeout:.0f} s")
    for key in HOTKEYS:
        add(f"hotkey {key[:2]}", any(key in m for m in msgs), "")
    if duplex:
        add("duplex audio started", any("[duplex audio" in m for m in msgs), "")
    if ready:
        before = state()
        t_end = clock() + stable
        while clock() < t_end:
            sleep(1.0)
        after = state()
        same = (before.get("pid"), before.get("runs")) == (
            after.get("pid"),
            after.get("runs"),
        ) and after.get("pid") not in (None, "")
        add(
            f"stable for {stable:.0f} s",
            same,
            f"pid {before.get('pid')}->{after.get('pid')}, runs {before.get('runs')}->{after.get('runs')}",
        )
    err = read_err_tail() or ""
    add(
        "no traceback at startup",
        "Traceback" not in err,
        "" if "Traceback" not in err else err[err.index("Traceback") :][:300],
    )
    return all(ok for _, ok, _ in results), results


def render(ok: bool, results: list[tuple[str, bool, str]]) -> str:
    lines = [
        f"{'ok  ' if r else 'FAIL'} {name}{(' - ' + d) if d else ''}" for name, r, d in results
    ]
    return "\n".join(lines + ["SMOKE " + ("PASS" if ok else "FAIL")])
