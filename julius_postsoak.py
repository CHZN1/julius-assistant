"""Automatic post-soak steps (Phase F4 -> G). One shot, scheduled for just after the 24 h soak ends (2026-11-09 00:13).

    python julius_postsoak.py schedule     install a one-shot LaunchAgent (com.julius.postsoak) for 2026-11-09 00:15 (runs on wake if the Mac is asleep then)
    python julius_postsoak.py unschedule   unload it (its plist file is left for you to delete)
    python julius_postsoak.py run          do the steps now (refuses before the soak has ended unless --force)

Steps, in order: soak report, log scan (both --since the soak start, before the restart), `julius_service.py smoke --restart`, event feed check on ws://127.0.0.1:8765, `julius_orb.py build`.
Results are appended to JULIUS_POSTSOAK.md. It then STOPS: it does not run the voice script, start the orb, or install the orb login item (`julius_orb.py on`); those wait for the user."""
import datetime
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = "/opt/miniconda3/envs/torch/bin/python"
LABEL = "com.julius.postsoak"
PLIST = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
LOG_DIR = Path("~/Library/Logs/Julius").expanduser()
DONE = LOG_DIR / "postsoak.done"
SOAK_START = "2026-11-08T00:13"
SOAK_END = "2026-11-09T00:13:48"
RESULTS = ROOT / "JULIUS_POSTSOAK.md"
FEED_PORT = 8765


def feed_check(port=FEED_PORT, timeout=30.0, sleep=time.sleep, clock=time.monotonic):
    """Connect to the event feed and wait for its hello. Returns (ok, detail)."""
    import julius_events as ev, julius_events_client as cl
    end = clock() + timeout; last = "no answer"
    while clock() < end:
        try:
            sock, rest = cl.connect(port); sock.settimeout(5)
            try:
                hello = next(cl.events(sock, rest), None)
            finally: sock.close()
            if hello and hello.get("type") == "hello" and hello.get("state") in ev.STATES:
                return True, f"hello received on ws://127.0.0.1:{port}: state={hello['state']}, {len(hello.get('transcript', []))} recent lines"
            last = f"unexpected first message: {hello}"
        except (OSError, ConnectionError, ValueError) as e: last = f"{type(e).__name__}: {e}"
        sleep(2.0)
    return False, last


def sh(args, timeout=900):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return r.returncode == 0, (r.stdout + (("\n" + r.stderr) if r.stderr.strip() else "")).strip()
    except subprocess.TimeoutExpired: return False, f"timed out after {timeout} s"


def steps():
    return [("Soak report (since the soak start)", lambda: sh([PY, "julius_soak.py", "report", "--since", SOAK_START])),
            ("Log scan (since the soak start)", lambda: sh([PY, "julius_logscan.py", "--since", SOAK_START])),
            ("Service smoke check with restart", lambda: sh([PY, "julius_service.py", "smoke", "--restart"], 400)),
            ("Event feed check", lambda: feed_check()),
            ("Orb app build", lambda: sh([PY, "julius_orb.py", "build"], 600))]


def render(results, when):
    ok_all = all(ok for _, ok, _ in results)
    out = [f"\n## Post-soak results (automatic run, {when})\n", f"**{'All five steps passed' if ok_all else 'Some steps FAILED, see below'}.**\n"]
    for name, ok, text in results:
        body = text if len(text) <= 5000 else text[:2500] + "\n[... trimmed ...]\n" + text[-2500:]
        out.append(f"### {'PASS' if ok else 'FAIL'}: {name}\n```\n{body}\n```\n")
    out.append("**Stopped here.** Waiting for the user: the voice script above, the first live orb run (`python julius_orb.py run`), and `python julius_orb.py on`. Nothing of those was started.\n")
    return "\n".join(out)


def run(force=False, now=None, step_list=None, results_path=RESULTS):
    now = now or datetime.datetime.now()
    if not force and now.strftime("%Y-%m-%dT%H:%M:%S") < SOAK_END: return False, "the soak has not ended yet"
    if not force and DONE.exists(): return False, "already ran"
    results = []
    for name, fn in (step_list or steps()):
        try: ok, text = fn()
        except Exception as e: ok, text = False, f"{type(e).__name__}: {e}"
        results.append((name, ok, text))
    with open(results_path, "a") as f: f.write(render(results, now.strftime("%Y-%m-%d %H:%M")))
    try: LOG_DIR.mkdir(parents=True, exist_ok=True); DONE.write_text(now.isoformat())
    except OSError: pass
    return True, "done"


def plist_dict():
    return {"Label": LABEL, "ProgramArguments": [PY, str(ROOT / "julius_postsoak.py"), "run"], "WorkingDirectory": str(ROOT),
            "StartCalendarInterval": {"Month": 11, "Day": 9, "Hour": 0, "Minute": 15}, "RunAtLoad": False, "LimitLoadToSessionType": "Aqua",
            "EnvironmentVariables": {"PATH": "/opt/miniconda3/envs/torch/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
            "StandardOutPath": str(LOG_DIR / "postsoak.out"), "StandardErrorPath": str(LOG_DIR / "postsoak.err")}


def main(argv):
    cmd = (argv[1:2] or ["run"])[0]; dom = f"gui/{os.getuid()}"
    if cmd == "schedule":
        LOG_DIR.mkdir(parents=True, exist_ok=True); PLIST.write_bytes(plistlib.dumps(plist_dict()))
        subprocess.run(["launchctl", "bootout", f"{dom}/{LABEL}"], capture_output=True); r = subprocess.run(["launchctl", "bootstrap", dom, str(PLIST)], capture_output=True, text=True)
        print("scheduled for 2026-11-09 00:15" if r.returncode == 0 else f"could not schedule: {r.stderr.strip()}"); return r.returncode
    if cmd == "unschedule": subprocess.run(["launchctl", "bootout", f"{dom}/{LABEL}"], capture_output=True); print(f"unloaded; {PLIST} left in place"); return 0
    ok, msg = run(force="--force" in argv); print(msg)
    if ok: subprocess.run(["launchctl", "bootout", f"{dom}/{LABEL}"], capture_output=True)   # one shot: unload itself (the plist file stays)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
