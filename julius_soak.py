"""Soak sampler: watches the Julius service for hours or days and reports CPU, memory, restarts, crashes and every logged problem.

    python julius_soak.py install     start sampling every 60 s in the background (its own launchd agent, com.julius.soak; read-only, never touches the service)
    python julius_soak.py status      is it running, how many samples
    python julius_soak.py report      summary of everything sampled so far (or --since 2026-11-06T18:00)
    python julius_soak.py uninstall   stop sampling and remove the agent (the samples in ~/Library/Logs/Julius/soak.jsonl stay until you delete them)
    python julius_soak.py run         the sampling loop itself (what the agent runs)

Samples: pid, launchd state and run counter (a changed pid or a higher run counter is a restart), CPU % over the last interval (service process and its children),
resident memory, threads, open files, free disk. Problems come from julius_logscan (log categories, tracebacks in launchd stderr)."""
import argparse, json, os, plistlib, statistics, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = Path("~/Library/Logs/Julius").expanduser()
SAMPLES = LOG_DIR / "soak.jsonl"
LABEL = "com.julius.soak"
PLIST = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
SERVICE = "com.julius.assistant"
PY = "/opt/miniconda3/envs/torch/bin/python"


def _domain(): return f"gui/{os.getuid()}"


def service_state(runner=subprocess.run):
    r = runner(["launchctl", "print", f"{_domain()}/{SERVICE}"], capture_output=True, text=True)
    out = {"loaded": r.returncode == 0}
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        for key in ("state", "pid", "runs", "last exit code"):
            if line.startswith(key + " ="): out[key.replace(" ", "_")] = line.split("=", 1)[1].strip()
    return out


class Sampler:
    def __init__(self, psutil_module=None):
        import psutil
        self.ps = psutil_module or psutil; self.last = None   # (pid, cpu_seconds, wall)

    def sample(self, state=None):
        state = state if state is not None else service_state()
        now = time.time(); row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "t": round(now, 1), "state": state.get("state", "missing"), "runs": state.get("runs"), "pid": None}
        try: pid = int(state.get("pid")) if state.get("pid") else None
        except ValueError: pid = None
        row["pid"] = pid
        if pid:
            try:
                p = self.ps.Process(pid); procs = [p] + p.children(recursive=True)
                cpu = sum(sum(x.cpu_times()[:2]) for x in procs if x.is_running())
                row["rss_mb"] = round(sum(x.memory_info().rss for x in procs if x.is_running()) / 1e6, 1); row["threads"] = sum(x.num_threads() for x in procs if x.is_running())
                try: row["open_files"] = len(p.open_files())
                except Exception: row["open_files"] = None
                if self.last and self.last[0] == pid and now > self.last[2]: row["cpu_pct"] = round(100 * (cpu - self.last[1]) / (now - self.last[2]), 1)
                self.last = (pid, cpu, now)
            except (self.ps.NoSuchProcess, self.ps.AccessDenied): row["gone"] = True; self.last = None
        else: self.last = None
        try: row["disk_free_gb"] = round(self.ps.disk_usage("/").free / 1e9, 1)
        except Exception: pass
        return row


def run(interval=60, path=SAMPLES, limit_hours=None):
    path.parent.mkdir(parents=True, exist_ok=True); s = Sampler(); started = time.time()
    while True:
        row = s.sample()
        with path.open("a") as f: f.write(json.dumps(row) + "\n")
        if limit_hours and time.time() - started > limit_hours * 3600: return
        time.sleep(interval)


def load(path=SAMPLES, since=None):
    rows = []
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            try: r = json.loads(line)
            except ValueError: continue
            if since and r["ts"] < since: continue
            rows.append(r)
    return rows


def summarize(rows, interval=60):
    """Restarts, gaps, CPU and memory statistics, and the memory trend (MB per hour, least squares over samples with a stable pid)."""
    if not rows: return {"samples": 0}
    restarts, down, prev_pid = 0, 0, None
    for r in rows:
        if r["pid"] and prev_pid and r["pid"] != prev_pid: restarts += 1
        if r["pid"]: prev_pid = r["pid"]
        if not r["pid"] or r.get("gone"): down += 1
    runs = [int(r["runs"]) for r in rows if r.get("runs") and str(r["runs"]).isdigit()]
    gaps = [b["t"] - a["t"] for a, b in zip(rows, rows[1:])]
    cpu = [r["cpu_pct"] for r in rows if r.get("cpu_pct") is not None]
    rss = [(r["t"], r["rss_mb"]) for r in rows if r.get("rss_mb")]
    slope = None
    if len(rss) > 5:   # trend within the longest run of one pid, so a restart does not look like a leak
        by_pid = {}
        for r in rows:
            if r.get("rss_mb") and r["pid"]: by_pid.setdefault(r["pid"], []).append((r["t"], r["rss_mb"]))
        longest = max(by_pid.values(), key=len)
        if len(longest) > 5:
            xs = [(t - longest[0][0]) / 3600 for t, _ in longest]; ys = [m for _, m in longest]; mx, my = statistics.mean(xs), statistics.mean(ys)
            den = sum((x - mx) ** 2 for x in xs); slope = round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den, 2) if den else 0.0
    return {"samples": len(rows), "from": rows[0]["ts"], "to": rows[-1]["ts"], "hours": round((rows[-1]["t"] - rows[0]["t"]) / 3600, 2),
            "restarts_seen": restarts, "run_counter_delta": (max(runs) - min(runs)) if runs else None, "samples_without_service": down, "longest_sampler_gap_s": round(max(gaps), 0) if gaps else 0,
            "cpu_pct": {"mean": round(statistics.mean(cpu), 1), "p95": round(sorted(cpu)[int(.95 * (len(cpu) - 1))], 1), "max": max(cpu)} if cpu else None,
            "rss_mb": {"min": min(m for _, m in rss), "max": max(m for _, m in rss), "last": rss[-1][1], "trend_mb_per_hour": slope} if rss else None,
            "threads_max": max((r.get("threads") or 0) for r in rows), "open_files_max": max((r.get("open_files") or 0) for r in rows),
            "disk_free_gb_min": min((r.get("disk_free_gb") for r in rows if r.get("disk_free_gb") is not None), default=None)}


def report(since=None):
    import julius_logscan
    s = summarize(load(since=since)); lines = ["Julius soak report"]
    if not s["samples"]: return "no samples yet (python julius_soak.py install)"
    lines += [f"{s['samples']} samples over {s['hours']} h ({s['from'][:19]} to {s['to'][:19]}), longest sampler gap {s['longest_sampler_gap_s']:.0f}s",
              f"restarts seen: {s['restarts_seen']} (launchd run counter moved by {s['run_counter_delta']}); samples with the service down: {s['samples_without_service']}"]
    if s["cpu_pct"]: lines.append(f"CPU: mean {s['cpu_pct']['mean']}%, p95 {s['cpu_pct']['p95']}%, max {s['cpu_pct']['max']}% of one core")
    if s["rss_mb"]: lines.append(f"memory: {s['rss_mb']['min']} to {s['rss_mb']['max']} MB (now {s['rss_mb']['last']}), trend {s['rss_mb']['trend_mb_per_hour']} MB/hour within one run; threads max {s['threads_max']}, open files max {s['open_files_max']}; disk free min {s['disk_free_gb_min']} GB")
    lines += ["", julius_logscan.render(julius_logscan.scan(since=since))]
    return "\n".join(lines)


def install(runner=subprocess.run):
    d = {"Label": LABEL, "ProgramArguments": [PY, str(ROOT / "julius_soak.py"), "run"], "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True,
         "StandardOutPath": str(LOG_DIR / "soak.out"), "StandardErrorPath": str(LOG_DIR / "soak.err"), "ProcessType": "Background", "Nice": 10}
    LOG_DIR.mkdir(parents=True, exist_ok=True); PLIST.parent.mkdir(parents=True, exist_ok=True); PLIST.write_bytes(plistlib.dumps(d))
    runner(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True, text=True)
    r = runner(["launchctl", "bootstrap", _domain(), str(PLIST)], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def uninstall(runner=subprocess.run):
    runner(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True, text=True)
    if PLIST.exists(): PLIST.unlink()
    return True, "sampler stopped and removed"


def status(runner=subprocess.run):
    r = runner(["launchctl", "print", f"{_domain()}/{LABEL}"], capture_output=True, text=True); rows = load()
    return {"installed": PLIST.exists(), "running": r.returncode == 0 and "state = running" in (r.stdout or ""), "samples": len(rows), "last_sample": rows[-1]["ts"] if rows else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0]); ap.add_argument("cmd", choices=["install", "uninstall", "status", "report", "run"]); ap.add_argument("--since", default="")
    ap.add_argument("--interval", type=int, default=60); a = ap.parse_args(argv)
    if a.cmd == "run": run(a.interval); return 0
    if a.cmd == "report": print(report(a.since or None)); return 0
    if a.cmd == "status": print(status()); return 0
    ok, msg = install() if a.cmd == "install" else uninstall(); print(msg or ("ok" if ok else "failed")); return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
