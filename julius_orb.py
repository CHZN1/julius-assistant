"""Run the Julius orb (the floating UI) as a macOS login item, next to the Julius service (julius_service.py).

  python julius_orb.py build      build "Julius Orb.app" (ui/app/build.sh: generates ui/orb.html from the approved prototype, compiles the Swift shell)
  python julius_orb.py on         build if needed, install the LaunchAgent com.julius.orb and start it; starts at every login, restarts after a crash
  python julius_orb.py off        stop it and remove it from login (a clean quit from the menu bar is not restarted)
  python julius_orb.py status     installed? running? which feed?
  python julius_orb.py run        run the app in the foreground (development; add --feed ws://127.0.0.1:8766 for the synthetic source)
The orb does not need the service to be running first: it shows "Julius is offline" until the feed (ws://127.0.0.1:8765) answers, then follows it.
Show/hide with Ctrl+Option+O; the menu bar item (a small circle) has Show/Hide, Settings and Quit."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABEL = "com.julius.orb"
PLIST = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
APP = ROOT / "ui" / "app" / "build" / "Julius Orb.app"
BINARY = APP / "Contents" / "MacOS" / "JuliusOrb"
FEED = "ws://127.0.0.1:8765"


def plist_dict(binary=BINARY, feed=FEED, log_dir=None):
    log_dir = Path(log_dir or "~/Library/Logs/Julius").expanduser()
    return {"Label": LABEL, "ProgramArguments": [str(binary), "--feed", feed], "WorkingDirectory": str(ROOT),
            "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},   # restart after a crash, not after a clean quit
            "ThrottleInterval": 10, "ProcessType": "Interactive", "LimitLoadToSessionType": "Aqua",
            "StandardOutPath": str(log_dir / "orb.out"), "StandardErrorPath": str(log_dir / "orb.err")}


def domain(): return f"gui/{os.getuid()}"
def run(args, runner=subprocess.run): return runner(args, capture_output=True, text=True)


def build(runner=subprocess.run):
    r = runner(["/bin/bash", str(ROOT / "ui" / "app" / "build.sh")], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1] if (r.stdout + r.stderr).strip() else ""


def on(runner=subprocess.run, plist_path=PLIST, binary=BINARY):
    if not Path(binary).exists():
        ok, msg = build(runner)
        if not ok: return False, f"build failed: {msg}"
    d = plist_dict(binary); Path(d["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True); plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(d)); run(["launchctl", "bootout", f"{domain()}/{LABEL}"], runner)
    run(["launchctl", "enable", f"{domain()}/{LABEL}"], runner); r = run(["launchctl", "bootstrap", domain(), str(plist_path)], runner)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def off(runner=subprocess.run, plist_path=PLIST):
    run(["launchctl", "bootout", f"{domain()}/{LABEL}"], runner); run(["launchctl", "disable", f"{domain()}/{LABEL}"], runner)
    if plist_path.exists(): plist_path.unlink()
    return True, "stopped and removed from login"


def status(runner=subprocess.run, plist_path=PLIST):
    r = run(["launchctl", "print", f"{domain()}/{LABEL}"], runner)
    out = {"installed": plist_path.exists(), "built": BINARY.exists(), "loaded": r.returncode == 0, "feed": FEED}
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        for key in ("state", "pid", "runs", "last exit code"):
            if line.startswith(key + " ="): out[key.replace(" ", "_")] = line.split("=", 1)[1].strip()
    return out


def main(argv):
    cmd = (argv[1:2] or ["status"])[0]
    if cmd == "build": ok, msg = build(); print(msg); return 0 if ok else 1
    if cmd == "on": ok, msg = on(); print("Julius orb on (starts at login)" if ok else f"could not start: {msg}"); return 0 if ok else 1
    if cmd == "off": print(off()[1]); return 0
    if cmd == "run":
        if not BINARY.exists(): print("not built: python julius_orb.py build"); return 1
        return subprocess.call([str(BINARY), *(argv[2:] or ["--feed", FEED])])
    print(status()); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
