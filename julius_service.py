"""Run Julius as a macOS login service (a LaunchAgent: it needs your audio devices, so it is not a system daemon).

  python julius_service.py on        install and start; starts at every login, restarts after a crash
  python julius_service.py off       stop and remove from login (a clean stop is not restarted)
  python julius_service.py status    loaded? running? pid, last exit, quiet mode, log file
  python julius_service.py restart
  python julius_service.py wake      leave quiet mode (same as the wake hotkey)
  python julius_service.py logs      follow the JSON log
No API key is written into the plist: Julius reads ANTHROPIC_API_KEY from the repo's .env like a normal run."""
import os, plistlib, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
LABEL = "com.julius.assistant"
PLIST = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
PY = "/opt/miniconda3/envs/torch/bin/python"

def plist_dict(root=ROOT, log_dir=None):
    log_dir = Path(log_dir or "~/Library/Logs/Julius").expanduser()
    return {"Label": LABEL, "ProgramArguments": ["/bin/zsh", str(root / "run_julius.sh")], "WorkingDirectory": str(root),
            "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False},  # restart after a crash or kill, not after a clean exit (off, Ctrl+C, /quit)
            "ThrottleInterval": 15, "ProcessType": "Interactive", "LimitLoadToSessionType": "Aqua",
            "EnvironmentVariables": {"JULIUS_SERVICE": "1", "JULIUS_PROFILE": "cloud", "PATH": "/opt/miniconda3/envs/torch/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
            "StandardOutPath": str(log_dir / "launchd.out"), "StandardErrorPath": str(log_dir / "launchd.err")}

def domain(): return f"gui/{os.getuid()}"
def run(args, runner=subprocess.run): return runner(args, capture_output=True, text=True)

def on(runner=subprocess.run, plist_path=PLIST):
    d = plist_dict(); Path(d["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True); plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_bytes(plistlib.dumps(d)); run(["launchctl", "bootout", f"{domain()}/{LABEL}"], runner)  # reload cleanly if already loaded
    run(["launchctl", "enable", f"{domain()}/{LABEL}"], runner); r = run(["launchctl", "bootstrap", domain(), str(plist_path)], runner)
    return r.returncode == 0, (r.stderr or r.stdout).strip()

def off(runner=subprocess.run, plist_path=PLIST):
    run(["launchctl", "bootout", f"{domain()}/{LABEL}"], runner); run(["launchctl", "disable", f"{domain()}/{LABEL}"], runner)
    if plist_path.exists(): plist_path.unlink()
    return True, "stopped and removed from login"

def status(runner=subprocess.run):
    r = run(["launchctl", "print", f"{domain()}/{LABEL}"], runner)
    if r.returncode != 0: return {"installed": PLIST.exists(), "loaded": False}
    out = {"installed": True, "loaded": True}
    for line in r.stdout.splitlines():
        line = line.strip()
        for key in ("state", "pid", "last exit code", "runs"):
            if line.startswith(key + " ="): out[key.replace(" ", "_")] = line.split("=", 1)[1].strip()
    import julius_state; out["quiet"] = julius_state.is_quiet(); out["log"] = "~/Library/Logs/Julius/julius.jsonl"; return out

def wake(runner=subprocess.run): return run(["launchctl", "kill", "SIGUSR1", f"{domain()}/{LABEL}"], runner).returncode == 0

def main(argv):
    cmd = (argv[1:2] or ["status"])[0]
    if cmd == "on": ok, msg = on(); print("Julius service on" if ok else f"could not start: {msg}"); return 0 if ok else 1
    if cmd == "off": print(off()[1]); return 0
    if cmd == "restart": run(["launchctl", "kickstart", "-k", f"{domain()}/{LABEL}"]); print("restarting"); return 0
    if cmd == "wake": print("woke" if wake() else "service not running"); return 0
    if cmd == "logs": os.execvp("tail", ["tail", "-n", "40", "-F", str(Path("~/Library/Logs/Julius/julius.jsonl").expanduser())])
    print(status()); return 0
if __name__ == "__main__": sys.exit(main(sys.argv))
