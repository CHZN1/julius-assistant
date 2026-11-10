"""Typed (no mic, no playback) first-audio benchmark of the tool-policy turns, for the MLX cloud profile.

Usage: python diagnostics/bench_typed_policy.py <label>   (appends to diagnostics/typed_policy_benchmarks.jsonl)
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
label = sys.argv[1]
TURNS = [
    ("warmup", "Hey there."),
    ("weather", "What's the weather in Springfield?"),
    ("tomorrow", "and what about tomorrow?"),
    ("calc", "what's 17% of 2480 again?"),
    ("explain", "explain overfitting simply"),
    ("eth", "what's ETH at right now?"),
]
work = Path(tempfile.mkdtemp())
(work / "script.json").write_text(json.dumps([t[1] for t in TURNS]))
env = dict(
    os.environ,
    JULIUS_PROFILE="cloud",
    JULIUS_VALIDATION_NO_DEVICES="1",
    JULIUS_VALIDATION_SCRIPT=str(work / "script.json"),
    JULIUS_VALIDATION_OUTPUT=str(work / "turns.json"),
    JULIUS_MEMORY_DIR=str(work / "mem"),
    JULIUS_COSTS_FILE=str(ROOT / "julius_memory" / "costs.jsonl"),
)
proc = subprocess.run(
    ["./run_julius.sh"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900
)
(work / "out.log").write_text(proc.stdout + proc.stderr)
turns = json.loads((work / "turns.json").read_text()) if (work / "turns.json").exists() else []
print(f"label={label} exit={proc.returncode} turns={len(turns)} log={work / 'out.log'}")
with open(ROOT / "diagnostics" / "typed_policy_benchmarks.jsonl", "a") as f:
    for (name, text), t in zip(TURNS, turns):
        cost = t.get("cost_by_tier_usd") or {}
        row = dict(
            label=label,
            turn=name,
            prompt=text,
            first_audio_s=round(t["processing_to_first_audio_seconds"] or -1, 2),
            brain_first_token_s=round(t.get("brain_first_token_seconds") or -1, 2),
            total_s=round(t["seconds"], 2),
            tools=[d.get("tool") for d in t.get("dispatcher_tasks", [])],
            cost_usd=round(sum(cost.values()), 5),
            answer=(t["answer"] or "")[:100],
        )
        print(row)
        f.write(json.dumps(row) + "\n")
