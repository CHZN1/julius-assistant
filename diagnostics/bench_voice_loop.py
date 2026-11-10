"""Typed (no microphone, no playback) time-to-first-audio benchmark of the full loop for one profile.

Usage: python diagnostics/bench_voice_loop.py cloud|cloud-torch
Runs Julius via run_julius.sh with scripted typed turns and prints per-turn timings from the turn log.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
profile = sys.argv[1]
TURNS = [
    ("warmup", "Hey there."),
    ("chat", "Hey Julius, how's it going?"),
    ("tool", "Who won the Giants game yesterday?"),
    (
        "planner",
        "Plan a three day trip to Tokyo for me, including the weather and what time it is there right now.",
    ),
    ("chat2", "Explain overfitting in one sentence."),
]
work = Path(tempfile.mkdtemp())
(work / "script.json").write_text(json.dumps([t[1] for t in TURNS]))
env = dict(
    os.environ,
    JULIUS_PROFILE=profile,
    JULIUS_VALIDATION_NO_DEVICES="1",
    JULIUS_VALIDATION_SCRIPT=str(work / "script.json"),
    JULIUS_VALIDATION_OUTPUT=str(work / "turns.json"),
    JULIUS_MEMORY_DIR=str(work / "mem"),
    JULIUS_COSTS_FILE=str(ROOT / "julius_memory" / "costs.jsonl"),
)
t0 = time.time()
proc = subprocess.run(
    ["./run_julius.sh"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900
)
(work / "out.log").write_text(proc.stdout + proc.stderr)
turns = json.loads((work / "turns.json").read_text()) if (work / "turns.json").exists() else []
ready = next((l for l in (proc.stdout).splitlines() if "julius ready" in l), "")
print(
    f"profile={profile} exit={proc.returncode} total={time.time() - t0:.0f}s turns={len(turns)} log={work / 'out.log'}"
)
rows = []
for (name, text), t in zip(TURNS, turns):
    row = dict(
        profile=profile,
        turn=name,
        first_audio_s=round(t["processing_to_first_audio_seconds"] or -1, 2),
        brain_ttft_s=round(t.get("brain_first_token_seconds") or -1, 2),
        total_s=round(t["seconds"], 2),
        cost=round(sum((t.get("cost_by_tier_usd") or {}).values()), 5),
        error=t["error"],
        answer=(t["answer"] or "")[:90],
    )
    rows.append(row)
    print(row)
out = ROOT / "diagnostics" / "voice_loop_benchmarks.jsonl"
with open(out, "a") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
