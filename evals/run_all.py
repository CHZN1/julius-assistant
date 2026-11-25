"""One command for every eval: unit tests, routing, file agent, recorded-audio regression. Prints one summary table.

A suite is flagged REGRESSION when a case that passed last run fails now, or its headline score drops past a tolerance.
Usage: python evals/run_all.py [--free] [--skip audio,routing,planner,code] [--runs 3]
  --free  runs only the suites that cost nothing (unit tests, deterministic routing stage, file agent); Haiku and audio are skipped
Exit status is 1 when anything regressed or a suite failed to run."""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RESULTS = HERE / "results"
PY = sys.executable


def latest_summary(prefix):
    files = sorted(RESULTS.glob(f"{prefix}_*.summary.json"))
    return (files[-1], json.loads(files[-1].read_text())) if files else (None, None)


def run(cmd, label):
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return proc, round(time.perf_counter() - t0, 1)


def unit():
    proc, secs = run([PY, "-m", "unittest", "discover", "-p", "test_*.py"], "unit")
    tail = proc.stderr.strip().splitlines()[-3:]
    m = re.search(r"Ran (\d+) tests", proc.stderr)
    n = int(m.group(1)) if m else 0
    ok = proc.returncode == 0
    return {
        "suite": "unit tests",
        "score": f"{n} tests" if ok else " ".join(tail)[:60],
        "metric": n,
        "regressed": not ok,
        "why": "" if ok else "tests failing",
        "seconds": secs,
        "usd": 0.0,
        "detail": "",
    }


def suite(name, script, args, prefix, key, label, tolerance, paid):
    old_path, old = latest_summary(prefix)
    proc, secs = run([PY, str(HERE / script)] + args, name)
    path, new = latest_summary(prefix)
    if proc.returncode not in (0, 1) or new is None or path == old_path:
        return {
            "suite": name,
            "score": "DID NOT RUN",
            "regressed": True,
            "why": (proc.stderr or proc.stdout)[-120:].replace("\n", " "),
            "seconds": secs,
            "usd": 0.0,
            "detail": "",
        }
    value = new.get(key)
    why = []
    broke = (new.get("regressions_vs_previous") or {}).get("newly_failing") or []
    if broke:
        why.append("now failing: " + ",".join(broke[:8]))
    if "baseline_regression" in new:  # judged against a stored baseline, not the previous run
        if new["baseline_regression"]:
            why.append(new.get("baseline_why") or f"{label} above baseline")
    elif old and old.get(key) is not None and value is not None:
        drop = (old[key] - value) if key != "mean_wer" else (value - old[key]) * 100
        if drop > tolerance:
            why.append(f"{label} {old[key]} -> {value}")
    extras = ""
    if prefix == "audio":
        extras = f"WER {new.get('mean_wer')}, first audio median {new.get('first_audio_median_s')}s, ${new.get('cost_per_turn_mean_usd')}/turn"
    if prefix == "routing":
        extras = f"{new.get('varying_cases')} varying, ${new.get('total_usd')}"
    return {
        "suite": name,
        "score": f"{label} {value}" + ("%" if key.endswith("pct") else ""),
        "metric": value,
        "regressed": bool(why),
        "why": "; ".join(why),
        "seconds": secs,
        "usd": new.get("total_usd") or 0.0,
        "detail": extras,
    }


def planned_suites(a, skip):
    """The suites to run for these options as (suite() arguments) tuples; None stands for the unit tests."""
    free = a.free
    wake_real = ROOT / "diagnostics" / "wake_real"
    plan = []
    if "unit" not in skip:
        plan.append(None)
    if "routing" not in skip:
        plan.append(
            (
                "routing" + (" (shortcuts only)" if free else ""),
                "routing_eval.py",
                ["--no-brain"] if free else ["--runs", a.runs],
                "partial_routing" if free else "routing",
                "ok_pct",
                "correct",
                2.0,
                not free,
            )
        )
    if "wake" not in skip and wake_real.is_dir():
        plan.append(("wake words", "wake_regression.py", [], "wake", "ok_pct", "ok", 0.0, False))
    if "wake" not in skip and not free and (wake_real / "wake3").is_dir():
        plan.append(
            ("wake cascade", "wake_eval.py", ["suite"], "wakecascade", "ok_pct", "ok", 3.4, False)
        )
    if "file" not in skip:
        plan.append(("file agent", "file_eval.py", [], "file", "ok_pct", "pass", 0.0, False))
    if not free:
        plan += paid_suites(skip)
    if "voice" not in skip and (ROOT / "voice_data").is_dir():
        # recorded multi-turn conversations (evals/voice_record.py); free
        plan.append(
            ("voice conversations", "voice_eval.py", [], "voice", "ok_pct", "handled", 1.0, False)
        )
        plan.append(
            (
                "voice front-end replay",
                "voice_eval.py",
                ["replay", "--mode", "gate"],
                "voicereplay_gate",
                "mean_wer",
                "WER",
                3.0,
                False,
            )
        )
    if "memory" not in skip:
        plan.append(
            (
                "memory" + (" (free parts)" if free else ""),
                "memory_eval.py",
                ["--free"] if free else [],
                "memory",
                "ok_pct",
                "sections ok",
                0.0,
                not free,
            )
        )
    return plan


def paid_suites(skip):
    """The suites that call the paid API (planner, research, coding agent, recorded audio)."""
    plan = []
    if "planner" not in skip:
        plan.append(
            ("planner (paid)", "planner_eval.py", [], "planner", "ok_pct", "clean runs", 0.0, True)
        )
    if "research" not in skip:
        plan.append(
            (
                "research (paid)",
                "research_eval.py",
                ["--runs", "1"],
                "research",
                "ok_pct",
                "cases",
                0.0,
                True,
            )
        )
    if "code" not in skip:
        plan.append(
            ("coding agent (paid)", "code_eval.py", [], "code", "ok_pct", "cases", 0.0, True)
        )
    if "audio" not in skip:
        plan.append(
            (
                "recorded audio",
                "audio_regression.py",
                [],
                "audio",
                "turn_ok_pct",
                "turns ok",
                3.0,
                True,
            )
        )
    return plan


def print_table(rows):
    w = max(len(r["suite"]) for r in rows)
    print(f"\n{'suite'.ljust(w)}  {'score':22} {'time':>7} {'cost':>8}  status")
    for r in rows:
        status = "REGRESSION: " + r["why"] if r["regressed"] else "ok"
        print(
            f"{r['suite'].ljust(w)}  {str(r['score']):22} {r['seconds']:6.0f}s ${r['usd']:<7.3f}  {status}"
        )
        if r["detail"]:
            print(f"{' ' * w}  {r['detail']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--free", action="store_true")
    ap.add_argument("--skip", default="")
    ap.add_argument("--runs", default="3")
    a = ap.parse_args()
    skip = set(filter(None, a.skip.split(",")))
    rows = [unit() if spec is None else suite(*spec) for spec in planned_suites(a, skip)]
    print_table(rows)
    bad = [r["suite"] for r in rows if r["regressed"]]
    print("\nALL CLEAR" if not bad else f"\nREGRESSIONS in: {', '.join(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
