"""Log scanner: summarizes what went wrong in Julius's service logs, by cause.

    python -m julius.service.logscan                  # everything in ~/Library/Logs/Julius (julius.jsonl + rotated files, launchd.err) and the wake log
    python -m julius.service.logscan --since 2026-11-05T20:00
    python -m julius.service.logscan --json

It reads redacted logs (no transcripts needed): every drop, error, restart and stall has a reason line. Counts are per category with first/last
example. Tracebacks in launchd.err are grouped by their last line. Used by the soak report (python -m julius.service.soak report)."""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

from julius.paths import REPO_ROOT

LOG_DIR = Path("~/Library/Logs/Julius").expanduser()
WAKE_LOG = REPO_ROOT / "julius_memory" / "wake_log.jsonl"

# (category, severity, pattern on the console message). First match wins. severity: problem | notice | info
CATEGORIES = [
    ("tts_unavailable", "problem", r"speech unavailable"),
    ("hotkey_unavailable", "problem", r"hotkey .*unavailable|not trusted"),
    ("traceback", "problem", r"Traceback \(most recent call last\)"),
    ("command_failed", "problem", r"command failed|\bfailed:|failed to"),
    (
        "cloud_error",
        "problem",
        r"couldn't complete that request|could not complete that request|rate.?limit|overloaded|APIError|APIConnection|timed out|timeout",
    ),
    ("audio_underflow", "problem", r"audio underflows this turn"),
    ("stall_forced_tool", "notice", r"stall detected"),
    ("echo_dropped", "notice", r"dropped as echo|ignored: Julius heard"),
    ("ignored_no_speech", "notice", r"ignored: (?:nothing transcribed|probably not speech)"),
    ("ignored_no_wake", "notice", r"ignored: no wake word"),
    ("wake_ignored_own_voice", "notice", r"wake ignored"),
    ("response_stopped", "notice", r"response stopped"),
    ("quiet_or_listening", "info", r"\[quiet:|listening again"),
    ("service_start", "info", r"^julius ready"),
    ("awake", "info", r"^\[awake\]"),
    ("asleep", "info", r"^\[asleep\]"),
]
COMPILED = [(name, sev, re.compile(pat, re.I)) for name, sev, pat in CATEGORIES]


def classify(msg, level="info"):
    msg = (msg or "").strip()
    for name, sev, rx in COMPILED:
        if rx.search(msg):
            return name, sev
    if level == "error":
        return "error_other", "problem"
    return None, None


def read_rows(log_dir=LOG_DIR, since=None):
    """All parsed console rows from julius.jsonl and its rotated backups, oldest first."""
    rows = []
    for p in sorted(
        Path(log_dir).glob("julius.jsonl*"), key=lambda p: p.name, reverse=True
    ):  # .5 ... .1, then the live file
        try:
            for line in p.read_text(errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if since and str(r.get("ts", "")) < since:
                    continue
                rows.append(r)
        except OSError:
            pass
    rows.sort(key=lambda r: str(r.get("ts", "")))
    return rows


TIMING = re.compile(r"\[timing: (.*)\]")


def latency(rows):
    """Per-turn timing lines -> p50/p95 of speech-end-to-first-audio (voice turns), ASR seconds and total, plus ASR worst-segment log-probability."""
    vals = collections.defaultdict(list)
    turns = errors = low_conf = 0
    for r in rows:
        m = TIMING.search(r.get("msg", ""))
        if not m:
            continue
        f = dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)
        turns += 1
        errors += f.get("error") == "yes"
        for key in (
            "first_audio",
            "speech_end_to_audio",
            "asr",
            "total",
            "asr_logprob",
            "no_speech",
        ):
            try:
                vals[key].append(float(f[key]))
            except (KeyError, ValueError):
                pass
        try:
            low_conf += float(f["asr_logprob"]) < -1.0
        except (KeyError, ValueError):
            pass
    pct = lambda xs, q: round(sorted(xs)[min(len(xs) - 1, int(q * len(xs)))], 2)
    return {
        "turns": turns,
        "errors": errors,
        "low_confidence_turns": low_conf,
        **{
            k: {"p50": pct(v, 0.5), "p95": pct(v, 0.95), "max": round(max(v), 2), "n": len(v)}
            for k, v in vals.items()
            if v
        },
    }


MARKS = "stderr_marks.json"


def mark_stderr(log_dir=LOG_DIR, now=None):
    """launchd's stderr/stdout files have no timestamps, so `--since` could not tell old tracebacks from new. Call this right before a restart or soak start:
    it records the files' current sizes with the time, and tracebacks(since=...) reads only what was written after the earliest mark at or after `since`."""
    import time as _t

    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    f = d / MARKS
    try:
        marks = json.loads(f.read_text())
    except (OSError, ValueError):
        marks = []
    marks.append(
        {
            "ts": now or _t.strftime("%Y-%m-%dT%H:%M:%S"),
            **{
                n: ((d / n).stat().st_size if (d / n).exists() else 0)
                for n in ("launchd.err", "launchd.out")
            },
        }
    )
    f.write_text(json.dumps(marks[-200:]))
    return marks[-1]


def _offsets(log_dir, since):
    if not since:
        return {}
    try:
        marks = json.loads((Path(log_dir) / MARKS).read_text())
    except (OSError, ValueError):
        return {}
    later = sorted((m for m in marks if m["ts"] >= since), key=lambda m: m["ts"])
    return later[0] if later else (marks[-1] if marks else {})


def tracebacks(log_dir=LOG_DIR, since=None):
    """Exceptions in the launchd stderr/stdout files, grouped by their final line (only what was written since the mark at or after `since`, when marks exist)."""
    out = collections.Counter()
    off = _offsets(log_dir, since)
    for name in ("launchd.err", "launchd.out"):
        p = Path(log_dir) / name
        if not p.exists():
            continue
        data = p.read_bytes()[off.get(name, 0) :].decode("utf-8", "replace")
        lines = data.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Traceback"):
                j = i + 1
                while j < len(lines) and (lines[j].startswith((" ", "\t")) or not lines[j].strip()):
                    j += 1
                last = lines[j] if j < len(lines) else "?"
                out[re.sub(r"0x[0-9a-f]+|\d+", "N", last)[:140]] += 1
    return out


def wake_stats(path=WAKE_LOG, since=None):
    """Stage-1 wake detector: fires, accepted, rejected, Whisper seconds spent on rejects, rejects whose text looks like the wake word."""
    s = {"fires": 0, "accepted": 0, "rejected": 0, "reject_whisper_s": 0.0, "wake_like_rejects": []}
    p = Path(path)
    if not p.exists():
        return s
    like = re.compile(r"\bjulius\b|^julius[A-Z]|juliet", re.I)
    since_w = since.replace("T", " ") if since else None
    for line in p.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") != "fire" or (since_w and r.get("ts", "") < since_w):
            continue
        s["fires"] += 1
        if r.get("accepted"):
            s["accepted"] += 1
        else:
            s["rejected"] += 1
            s["reject_whisper_s"] += r.get("whisper_s") or 0
            if r.get("heard") and like.search(r["heard"]):
                s["wake_like_rejects"].append((r["ts"], r["heard"][:60]))
    s["reject_whisper_s"] = round(s["reject_whisper_s"], 1)
    return s


def scan(log_dir=LOG_DIR, since=None, wake_log=WAKE_LOG):
    rows = read_rows(log_dir, since)
    cats = collections.defaultdict(
        lambda: {"count": 0, "severity": "", "first": "", "last": "", "example": ""}
    )
    underflow_bursts = 0
    for r in rows:
        msg = r.get("msg", "")
        name, sev = classify(msg, r.get("level", "info"))
        if not name:
            continue
        c = cats[name]
        c["count"] += 1
        c["severity"] = sev
        c["first"] = c["first"] or r["ts"]
        c["last"] = r["ts"]
        c["example"] = c["example"] or msg[:110]
    span = (rows[0]["ts"], rows[-1]["ts"]) if rows else ("", "")
    return {
        "rows": len(rows),
        "from": span[0],
        "to": span[1],
        "categories": dict(cats),
        "tracebacks": dict(tracebacks(log_dir, since)),
        "latency": latency(rows),
        "wake": wake_stats(wake_log, since),
    }


def render(report):
    out = [
        f"Julius log scan: {report['rows']} lines, {report['from'][:19]} to {report['to'][:19]}",
        "",
    ]
    order = {"problem": 0, "notice": 1, "info": 2}
    out.append(f"{'category':26} {'sev':8} {'count':>6}  first -> last")
    for name, c in sorted(
        report["categories"].items(),
        key=lambda kv: (order.get(kv[1]["severity"], 9), -kv[1]["count"]),
    ):
        out.append(
            f"{name:26} {c['severity']:8} {c['count']:>6}  {c['first'][5:19]} -> {c['last'][5:19]}"
        )
    if report["tracebacks"]:
        out += ["", "tracebacks (launchd stderr/stdout), by last line:"] + [
            f"  {n:4} {line}"
            for line, n in sorted(report["tracebacks"].items(), key=lambda kv: -kv[1])
        ]
    lat = report.get("latency") or {}
    if lat.get("turns"):
        out += [
            "",
            f"turn timing ({lat['turns']} turns, {lat['errors']} with errors, {lat['low_confidence_turns']} low-confidence ASR):",
        ]
        for key, label in (
            ("speech_end_to_audio", "speech end -> first audio"),
            ("first_audio", "processing -> first audio"),
            ("asr", "ASR"),
            ("total", "whole turn"),
        ):
            if key in lat:
                out.append(
                    f"  {label:26} p50 {lat[key]['p50']}s  p95 {lat[key]['p95']}s  max {lat[key]['max']}s  (n={lat[key]['n']})"
                )
    w = report["wake"]
    out += [
        "",
        f"wake detector: {w['fires']} fires, {w['accepted']} accepted, {w['rejected']} rejected ({w['reject_whisper_s']}s of Whisper on rejects)",
    ]
    for ts, heard in w["wake_like_rejects"][:8]:
        out.append(f"  rejected but looked like a wake: {ts[5:19]} {heard!r}")
    problems = sum(
        c["count"] for c in report["categories"].values() if c["severity"] == "problem"
    ) + sum(report["tracebacks"].values())
    out += ["", f"problems: {problems}"]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="")
    ap.add_argument("--dir", default=str(LOG_DIR))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    report = scan(a.dir, a.since or None)
    print(json.dumps(report, indent=1, default=str) if a.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
