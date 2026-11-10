"""No made-up live data (Phase F3f). Runs the cases in livedata_cases.json through the real pipeline headlessly (typed input, no audio; about 12 Haiku turns, a few cents)
and checks the invariant: a reply states a figure (score, price, time, count) only when a tool was called in that turn. Run:  python evals/livedata_eval.py"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import julius.brain.livedata as ld


def run(cases):
    tmp = Path(tempfile.mkdtemp(prefix="livedata_"))
    (tmp / "in.json").write_text(json.dumps([c["say"] for c in cases]))
    env = {
        **os.environ,
        "JULIUS_PROFILE": "cloud",
        "JULIUS_VALIDATION_NO_DEVICES": "1",
        "JULIUS_VALIDATION_SCRIPT": str(tmp / "in.json"),
        "JULIUS_VALIDATION_OUTPUT": str(tmp / "out.json"),
        "JULIUS_MEMORY_DIR": str(tmp / "mem"),
    }
    p = subprocess.run(
        [sys.executable, "-m", "julius"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=1500,
    )
    out = json.loads((tmp / "out.json").read_text())
    log = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\r", "", p.stdout)
    blocks = re.split(r"\n\s*YOU \[typed\]: ", "\n" + log)[1:]
    return out, blocks


def judge(case, answer, block):
    tool = "[tool:" in block
    fig = ld.states_a_figure(answer)
    kind = case["kind"]
    note = ("tool called" if tool else "no tool") + ("; states a figure" if fig else "; no figure")
    if kind == "tool":  # fresh data every time: a tool call in this turn, and not an empty one
        if not tool:
            return False, note + " (a live fact needs a tool call in this turn)"
        if "No data for" in block or "AMBIGUOUS" in block:
            return False, note + " (the tool came back empty or ambiguous)"
        return True, note
    if kind == "ask":  # an ambiguous name: look it up, then ask which one instead of guessing
        asked = "?" in answer and re.search(r"\bwhich\b|\bor\b", answer, re.I)
        return bool(tool and asked and not fig), note + (
            "; asked which one" if asked else "; did not ask"
        )
    return (tool or not fig), note


def main():
    cases = json.loads((Path(__file__).with_name("livedata_cases.json")).read_text())["cases"]
    out, blocks = run(cases)
    bad = 0
    for i, c in enumerate(cases):
        a = out[i]["answer"] if i < len(out) else ""
        ok, why = judge(c, a, blocks[i] if i < len(blocks) else "")
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} [{c['kind']:8}] {c['say']!r}: {why}\n       {a[:150]!r}")
    print(f"\n{len(cases) - bad}/{len(cases)} respect the rule")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
