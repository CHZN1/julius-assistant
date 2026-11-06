"""Run Julius as a macOS login service (a LaunchAgent: it needs your audio devices, so it is not a system daemon).

  python julius_service.py on        install and start; starts at every login, restarts after a crash
  python julius_service.py off       stop and remove from login (a clean stop is not restarted)
  python julius_service.py status    loaded? running? pid, last exit, quiet mode, log file
  python julius_service.py restart
  python julius_service.py wake      leave quiet mode (same as the wake hotkey)
  python julius_service.py logs [-n 60] [--raw] [--no-follow]   recent log lines, then new ones as they arrive (Ctrl+C to stop)
  python julius_service.py doctor    check, the way launchd starts Julius, whether macOS lets this Python see keys (the hotkeys)
No API key is written into the plist: Julius reads ANTHROPIC_API_KEY from the repo's .env like a normal run."""
import json, os, plistlib, subprocess, sys, time
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

LOG = Path("~/Library/Logs/Julius/julius.jsonl").expanduser()
def format_line(raw):
    """One JSON log line -> 'HH:MM:SS level message' (events show their fields)."""
    try: r = json.loads(raw)
    except ValueError: return raw.rstrip("\n")
    t = str(r.get("ts", ""))[11:19]; extra = {k: v for k, v in r.items() if k not in ("ts", "level", "event", "msg")}
    body = r.get("msg", "") if r.get("event") == "console" else f"{r.get('event')} {r.get('msg', '')} {extra if extra else ''}".strip()
    return f"{t} {str(r.get('level', '')):5} {body}"

def read_tail(path, n):
    try: lines = Path(path).read_text(errors="replace").splitlines()
    except OSError: return None
    return lines[-n:]

def show_logs(n=60, raw=False, follow=True, path=LOG, out=print, sleep=time.sleep, stop=lambda: False):
    lines = read_tail(path, n)
    if lines is None:
        out(f"no log yet at {path}. Is the service on? (python julius_service.py status)"); err = Path(path).parent / "launchd.err"
        if err.exists(): out("launchd.err says:"); [out("  " + l) for l in err.read_text(errors="replace").splitlines()[-8:]]
        return
    for l in lines: out(l if raw else format_line(l))
    if not follow: return
    pos = Path(path).stat().st_size
    while not stop():
        sleep(0.5)
        try: size = Path(path).stat().st_size
        except OSError: continue
        if size < pos: pos = 0  # rotated
        if size > pos:
            with open(path, errors="replace") as f:
                f.seek(pos); chunk = f.read(); pos = f.tell()
            for l in chunk.splitlines(): out(l if raw else format_line(l))

def doctor(runner=subprocess.run, sleep=time.sleep, workdir="/tmp"):
    """Start a throwaway LaunchAgent that asks pynput, from the real Python binary, whether macOS trusts it for keyboard events."""
    real = os.path.realpath(PY); label = "com.julius.doctor"; out_file = Path(workdir) / "julius_doctor.out"; out_file.unlink(missing_ok=True)
    code = "import sys;from pynput import keyboard;print('trusted' if keyboard.Listener.IS_TRUSTED else 'NOT trusted', sys.executable)"
    pl = Path(workdir) / "julius_doctor.plist"
    pl.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [real, "-c", code], "RunAtLoad": True, "LimitLoadToSessionType": "Aqua", "StandardOutPath": str(out_file), "StandardErrorPath": str(Path(workdir) / "julius_doctor.err")}))
    run(["launchctl", "bootout", f"{domain()}/{label}"], runner); run(["launchctl", "bootstrap", domain(), str(pl)], runner)
    for _ in range(20):
        sleep(0.5)
        if out_file.exists() and out_file.read_text().strip(): break
    run(["launchctl", "bootout", f"{domain()}/{label}"], runner)
    verdict = out_file.read_text().strip() if out_file.exists() else "no answer"
    return verdict, real

def main(argv):
    cmd = (argv[1:2] or ["status"])[0]
    if cmd == "on": ok, msg = on(); print("Julius service on" if ok else f"could not start: {msg}"); return 0 if ok else 1
    if cmd == "off": print(off()[1]); return 0
    if cmd == "restart": run(["launchctl", "kickstart", "-k", f"{domain()}/{LABEL}"]); print("restarting"); return 0
    if cmd == "wake": print("woke" if wake() else "service not running"); return 0
    if cmd == "logs":
        n = int(argv[argv.index("-n") + 1]) if "-n" in argv else 60
        try: show_logs(n, "--raw" in argv, "--no-follow" not in argv)
        except KeyboardInterrupt: pass
        return 0
    if cmd == "doctor":
        verdict, real = doctor(); print(f"launchd -> {verdict}")
        if verdict.startswith("trusted"): print("The hotkeys will work after: python julius_service.py restart")
        else: print(f"macOS does not trust this binary for keyboard events. In System Settings > Privacy & Security, add this exact file to BOTH Accessibility and Input Monitoring (+ button, Cmd+Shift+G, paste the path), switch them on, then run doctor again:\n  {real}\nIf an old entry for Python is already listed, remove it first and re-add this one.\nNo hotkey needed meanwhile: `python julius_service.py wake` does the same as the wake key.")
        return 0
    print(status()); return 0
if __name__ == "__main__": sys.exit(main(sys.argv))
