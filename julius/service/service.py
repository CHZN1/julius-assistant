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

import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from julius import state
from julius.paths import REPO_ROOT

ROOT = REPO_ROOT
LABEL = "com.julius.assistant"
PLIST = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
PY = "/opt/miniconda3/envs/torch/bin/python"


def plist_dict(root=ROOT, log_dir=None):
    log_dir = Path(log_dir or "~/Library/Logs/Julius").expanduser()
    return {
        "Label": LABEL,
        "ProgramArguments": ["/bin/zsh", str(root / "run_julius.sh")],
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
            "TMPDIR": str(state.tmp_dir()) + "/",
            "PATH": "/opt/miniconda3/envs/torch/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "StandardOutPath": str(log_dir / "launchd.out"),
        "StandardErrorPath": str(log_dir / "launchd.err"),
    }


def domain():
    return f"gui/{os.getuid()}"


def run(args, runner=subprocess.run):
    return runner(args, capture_output=True, text=True)


def on(runner=subprocess.run, plist_path=PLIST):
    d = plist_dict()
    Path(d["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(d))
    run(["launchctl", "bootout", f"{domain()}/{LABEL}"], runner)  # reload cleanly if already loaded
    run(["launchctl", "enable", f"{domain()}/{LABEL}"], runner)
    r = run(["launchctl", "bootstrap", domain(), str(plist_path)], runner)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def off(runner=subprocess.run, plist_path=PLIST):
    run(["launchctl", "bootout", f"{domain()}/{LABEL}"], runner)
    run(["launchctl", "disable", f"{domain()}/{LABEL}"], runner)
    if plist_path.exists():
        plist_path.unlink()
    return True, "stopped and removed from login"


def status(runner=subprocess.run):
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


def wake(runner=subprocess.run):
    return run(["launchctl", "kill", "SIGUSR1", f"{domain()}/{LABEL}"], runner).returncode == 0


LOG = Path("~/Library/Logs/Julius/julius.jsonl").expanduser()


def format_line(raw):
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


def read_tail(path, n):
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return None
    return lines[-n:]


def show_logs(
    n=60, raw=False, follow=True, path=LOG, out=print, sleep=time.sleep, stop=lambda: False
):
    lines = read_tail(path, n)
    if lines is None:
        out(f"no log yet at {path}. Is the service on? (python -m julius.service.service status)")
        err = Path(path).parent / "launchd.err"
        if err.exists():
            out("launchd.err says:")
            [out("  " + l) for l in err.read_text(errors="replace").splitlines()[-8:]]
        return
    for l in lines:
        out(l if raw else format_line(l))
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
            for l in chunk.splitlines():
                out(l if raw else format_line(l))


def doctor(
    runner=subprocess.run,
    sleep=time.sleep,
    workdir=None,
    binary=None,
    combos=("stop=ctrl+alt+s", "wake=ctrl+alt+j"),
):
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


def main(argv):
    cmd = (argv[1:2] or ["status"])[0]
    if cmd == "on":
        ok, msg = on()
        print("Julius service on" if ok else f"could not start: {msg}")
        return 0 if ok else 1
    if cmd == "off":
        print(off()[1])
        return 0
    if cmd == "restart":
        from julius.service import logscan

        logscan.mark_stderr()
        run(["launchctl", "kickstart", "-k", f"{domain()}/{LABEL}"])
        print("restarting")
        return 0
    if cmd == "wake":
        print("woke" if wake() else "service not running")
        return 0
    if cmd == "logs":
        n = int(argv[argv.index("-n") + 1]) if "-n" in argv else 60
        try:
            show_logs(n, "--raw" in argv, "--no-follow" not in argv)
        except KeyboardInterrupt:
            pass
        return 0
    if cmd == "smoke":
        import datetime

        from julius import config
        from julius.service import smoke

        err_path = Path("~/Library/Logs/Julius/launchd.err").expanduser()
        size = err_path.stat().st_size if err_path.exists() else 0
        if "--restart" in argv:
            from julius.service import logscan

            logscan.mark_stderr()
            start = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            run(["launchctl", "kickstart", "-k", f"{domain()}/{LABEL}"])
            time.sleep(2)
        else:
            import psutil

            pid = int(status().get("pid") or 0)
            start = (
                datetime.datetime.fromtimestamp(psutil.Process(pid).create_time()).strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                if pid
                else "9999"
            )
            size = (
                err_path.stat().st_size if err_path.exists() else 0
            )  # no restart: stderr has no timestamps, so only what arrives from now on is checked (use --restart to check the whole startup)

        def err_tail():
            try:
                with err_path.open("rb") as f:
                    f.seek(size)
                    return f.read().decode("utf-8", "replace")
            except OSError:
                return ""

        def read_log():
            try:
                return LOG.read_text(errors="replace").splitlines()[-4000:]
            except OSError:
                return []

        duplex = bool((config.load_config().get("audio") or {}).get("duplex", True))
        ok, results = smoke.check(status, read_log, err_tail, start, duplex=duplex)
        print(smoke.render(ok, results))
        return 0 if ok else 1
    if cmd == "doctor":
        verdict, binary = doctor()
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
    print(status())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
