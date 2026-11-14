"""Run Julius as a macOS login service (a LaunchAgent: it needs your audio devices, so it is not a system daemon).

  python -m julius.service.service on        install and start; starts at every login, restarts after a crash
  python -m julius.service.service off       stop and remove from login (a clean stop is not restarted)
  python -m julius.service.service status    loaded? running? pid, last exit, quiet mode, log file
  python -m julius.service.service restart
  python -m julius.service.service wake      leave quiet mode (same as the wake hotkey)
  python -m julius.service.service logs [-n 60] [--raw] [--no-follow]   recent log lines, then new ones as they arrive (Ctrl+C to stop)
  python -m julius.service.service smoke [--restart]   startup smoke check: ready, hotkeys, duplex audio, no crash loop, no traceback (see julius_smoke.py)
  python -m julius.service.service doctor    check, the way launchd starts Julius, whether the stop and wake hotkeys register
No API key is written into the plist: Julius reads ANTHROPIC_API_KEY from the repo's .env like a normal run."""

import contextlib
import json
import os
import plistlib
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from julius import state
from julius.paths import REPO_ROOT
from julius.profile import CLOUD_DEFAULTS

ROOT = REPO_ROOT
LABEL = "com.julius.assistant"
PLIST = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
PY = sys.executable


def plist_dict(root: Path = ROOT, log_dir: str | None = None) -> dict[str, Any]:
    log_dir = Path(log_dir or "~/Library/Logs/Julius").expanduser()
    return {
        "Label": LABEL,
        # python directly, not via run_julius.sh: macOS attributes file access (the CHZN drive) to the launched binary, and a shell in between would make the Full Disk Access grant for this interpreter useless. run_julius.sh stays for manual runs.
        "ProgramArguments": [PY, "-u", "-m", "julius"],
        "WorkingDirectory": str(root),
        "RunAtLoad": True,
        "KeepAlive": {
            "SuccessfulExit": False
        },  # restart after a crash or kill, not after a clean exit (off, Ctrl+C, /quit)
        "ThrottleInterval": 15,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
        "EnvironmentVariables": {
            "JULIUS_SERVICE": "1",
            "JULIUS_PROFILE": "cloud",
            **CLOUD_DEFAULTS,
            "TMPDIR": str(state.tmp_dir()) + "/",
            "PATH": f"{Path(PY).parent}:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "StandardOutPath": str(log_dir / "launchd.out"),
        "StandardErrorPath": str(log_dir / "launchd.err"),
    }


def domain() -> str:
    return f"gui/{os.getuid()}"


def run(args: list[str], runner: Callable = subprocess.run) -> Any:
    return runner(args, capture_output=True, text=True)


def on(runner: Callable = subprocess.run, plist_path: Path = PLIST) -> tuple[bool, str]:
    d = plist_dict()
    Path(d["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(d))
    run(["launchctl", "bootout", f"{domain()}/{LABEL}"], runner)  # reload cleanly if already loaded
    run(["launchctl", "enable", f"{domain()}/{LABEL}"], runner)
    r = run(["launchctl", "bootstrap", domain(), str(plist_path)], runner)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def off(runner: Callable = subprocess.run, plist_path: Path = PLIST) -> Any:
    run(["launchctl", "bootout", f"{domain()}/{LABEL}"], runner)
    run(["launchctl", "disable", f"{domain()}/{LABEL}"], runner)
    if plist_path.exists():
        plist_path.unlink()
    return True, "stopped and removed from login"


def status(runner: Callable = subprocess.run) -> dict[str, str | bool]:
    r = run(["launchctl", "print", f"{domain()}/{LABEL}"], runner)
    if r.returncode != 0:
        return {"installed": PLIST.exists(), "loaded": False}
    out = {"installed": True, "loaded": True}
    for line in r.stdout.splitlines():
        line = line.strip()
        for key in ("state", "pid", "last exit code", "runs"):
            if line.startswith(key + " ="):
                out[key.replace(" ", "_")] = line.split("=", 1)[1].strip()
    from julius import state

    out["quiet"] = state.is_quiet()
    out["log"] = "~/Library/Logs/Julius/julius.jsonl"
    return out


def wake(runner: Callable = subprocess.run) -> bool:
    return run(["launchctl", "kill", "SIGUSR1", f"{domain()}/{LABEL}"], runner).returncode == 0


LOG = Path("~/Library/Logs/Julius/julius.jsonl").expanduser()


def format_line(raw: str) -> str:
    """One JSON log line -> 'HH:MM:SS level message' (events show their fields)."""
    try:
        r = json.loads(raw)
    except ValueError:
        return raw.rstrip("\n")
    t = str(r.get("ts", ""))[11:19]
    extra = {k: v for k, v in r.items() if k not in ("ts", "level", "event", "msg")}
    body = (
        r.get("msg", "")
        if r.get("event") == "console"
        else f"{r.get('event')} {r.get('msg', '')} {extra if extra else ''}".strip()
    )
    return f"{t} {str(r.get('level', '')):5} {body}"


def read_tail(path: Path, n: int) -> list[str]:
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return None
    return lines[-n:]


def show_logs(
    n: int = 60,
    raw: bool = False,
    follow: bool = True,
    path: Path = LOG,
    out: Callable = print,
    sleep: Callable = time.sleep,
    stop: Callable = lambda: False,
) -> None:
    lines = read_tail(path, n)
    if lines is None:
        out(f"no log yet at {path}. Is the service on? (python -m julius.service.service status)")
        err = Path(path).parent / "launchd.err"
        if err.exists():
            out("launchd.err says:")
            [out("  " + line) for line in err.read_text(errors="replace").splitlines()[-8:]]
        return
    for line in lines:
        out(line if raw else format_line(line))
    if not follow:
        return
    pos = Path(path).stat().st_size
    while not stop():
        sleep(0.5)
        try:
            size = Path(path).stat().st_size
        except OSError:
            continue
        if size < pos:
            pos = 0  # rotated
        if size > pos:
            with open(path, errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
            for line in chunk.splitlines():
                out(line if raw else format_line(line))


def doctor(
    runner: Callable = subprocess.run,
    sleep: Callable = time.sleep,
    workdir: str | None = None,
    binary: str | None = None,
    combos: tuple[str, str] = ("stop=ctrl+alt+s", "wake=ctrl+alt+j"),
) -> tuple[str, str]:
    """Start the hotkey helper the way launchd would and report whether both hotkeys registered (Carbon hotkeys need no Accessibility or Input Monitoring permission)."""
    from julius.service import hotkey

    workdir = workdir or str(state.tmp_dir())
    label = "com.julius.doctor"
    out_file = Path(workdir) / "julius_doctor.out"
    out_file.unlink(missing_ok=True)
    try:
        binary = binary or hotkey.build()
    except Exception as e:
        return f"cannot build the hotkey helper: {e}", ""
    pl = Path(workdir) / "julius_doctor.plist"
    pl.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": [str(binary), *combos],
                "RunAtLoad": True,
                "LimitLoadToSessionType": "Aqua",
                "EnvironmentVariables": {"JULIUS_HOTKEYD_NOWATCH": "1"},
                "StandardOutPath": str(out_file),
                "StandardErrorPath": str(Path(workdir) / "julius_doctor.err"),
            }
        )
    )
    run(["launchctl", "bootout", f"{domain()}/{label}"], runner)
    run(["launchctl", "bootstrap", domain(), str(pl)], runner)
    for _ in range(20):
        sleep(0.5)
        if out_file.exists() and "ready" in out_file.read_text():
            break
    run(["launchctl", "bootout", f"{domain()}/{label}"], runner)
    verdict = (
        " ".join(out_file.read_text().split())
        if out_file.exists() and out_file.read_text().strip()
        else "no answer"
    )
    return verdict, str(binary)


def _cmd_on(_argv: list[str]) -> int:
    ok, msg = on()
    print("Julius service on" if ok else f"could not start: {msg}")
    return 0 if ok else 1


def _cmd_off(_argv: list[str]) -> int:
    print(off()[1])
    return 0


def _cmd_restart(_argv: list[str]) -> int:
    from julius.service import logscan

    logscan.mark_stderr()
    run(["launchctl", "kickstart", "-k", f"{domain()}/{LABEL}"])
    print("restarting")
    return 0


def _cmd_wake(_argv: list[str]) -> int:
    print("woke" if wake() else "service not running")
    return 0


def _cmd_logs(argv: list[str]) -> int:
    n = int(argv[argv.index("-n") + 1]) if "-n" in argv else 60
    with contextlib.suppress(KeyboardInterrupt):
        show_logs(n, "--raw" in argv, "--no-follow" not in argv)
    return 0


def _smoke_window(argv: list[str], err_path: Any) -> Any:
    """(start timestamp, stderr offset) from which the smoke check looks."""
    import datetime

    stamp = "%Y-%m-%dT%H:%M:%S"
    if "--restart" in argv:
        from julius.service import logscan

        size = err_path.stat().st_size if err_path.exists() else 0
        logscan.mark_stderr()
        start = datetime.datetime.now().strftime(stamp)
        run(["launchctl", "kickstart", "-k", f"{domain()}/{LABEL}"])
        time.sleep(2)
        return start, size
    import psutil

    pid = int(status().get("pid") or 0)
    start = (
        datetime.datetime.fromtimestamp(psutil.Process(pid).create_time()).strftime(stamp)
        if pid
        else "9999"
    )
    # no restart: stderr has no timestamps, so only what arrives from now on is checked (use --restart to check the whole startup)
    return start, err_path.stat().st_size if err_path.exists() else 0


def _cmd_smoke(argv: list[str]) -> int:
    from julius import config
    from julius.service import smoke

    err_path = Path("~/Library/Logs/Julius/launchd.err").expanduser()
    start, size = _smoke_window(argv, err_path)

    def err_tail() -> Any:
        try:
            with err_path.open("rb") as f:
                f.seek(size)
                return f.read().decode("utf-8", "replace")
        except OSError:
            return ""

    def read_log() -> Any:
        try:
            return LOG.read_text(errors="replace").splitlines()[-4000:]
        except OSError:
            return []

    duplex = bool((config.load_config().get("audio") or {}).get("duplex", True))
    ok, results = smoke.check(status, read_log, err_tail, start, duplex=duplex)
    print(smoke.render(ok, results))
    return 0 if ok else 1


def _cmd_doctor(_argv: list[str]) -> int:
    verdict, _binary = doctor()
    print(f"hotkey helper under launchd -> {verdict}")
    if verdict.endswith("ready stop wake") and "failed" not in verdict:
        print(
            "Both hotkeys register (no Accessibility or Input Monitoring needed): Ctrl+Option+S stops, Ctrl+Option+J wakes."
        )
    else:
        print(
            "A hotkey did not register (another app may already own that key combination, or the helper could not start). Details above; no hotkey needed meanwhile: `python -m julius.service.service wake` does the same as the wake key."
        )
    return 0


_COMMANDS = {
    "on": _cmd_on,
    "off": _cmd_off,
    "restart": _cmd_restart,
    "wake": _cmd_wake,
    "logs": _cmd_logs,
    "smoke": _cmd_smoke,
    "doctor": _cmd_doctor,
}


def main(argv: list[str]) -> Any:
    """Run one service subcommand (on, off, status, restart, wake, logs, smoke, doctor)."""
    command = _COMMANDS.get((argv[1:2] or ["status"])[0])
    if command is None:
        print(status())
        return 0
    return command(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
