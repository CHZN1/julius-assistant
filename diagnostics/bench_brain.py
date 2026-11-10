"""Brain benchmark through Julius's real backend adapter and prompt paths.

Usage: bench_brain.py --label baseline --model llama3.2:3b
Each prompt is preceded by the same brain-release decision Julius makes before an ASR/voice phase
(keep_brain_loaded() policy when available, otherwise close_backend), so reload cost is measured.
Writes diagnostics/runs/<label>__<model>.csv and prints a summary.
"""

import argparse
import ast
import csv
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--label", required=True)
ap.add_argument("--model", required=True)
a = ap.parse_args()
os.environ["JULIUS_BACKEND"] = "ollama"
os.environ["JULIUS_MODEL"] = a.model
sys.path.insert(0, str(ROOT))
import julius_backend as jb
import julius_tools as jt
from julius_calculator import evaluate
from julius_conversation import conversation_messages


def candidate_system():
    src = ast.parse((ROOT / "julius_candidate.py").read_text())
    ns = {}
    for n in src.body:
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SYSTEM" for t in n.targets
        ):
            exec(compile(ast.Module(body=[n], type_ignores=[]), "<c>", "exec"), ns)
    from julius_vision_mode import vision_capability_prompt

    return ns["SYSTEM"] + vision_capability_prompt(False)


SYSTEM = candidate_system()

HIST = [
    {
        "role": "user",
        "content": "I'm building a CNN for emotion detection on FER2013 and it keeps getting 62 percent validation accuracy while training hits 95.",
    },
    {
        "role": "assistant",
        "content": "That gap is classic overfitting. Try stronger augmentation, dropout, and early stopping.",
    },
]
CHAT = [
    ("chitchat", "Hey Julius, how's it going?"),
    ("factual", "What's the difference between overfitting and underfitting?"),
    (
        "multistep",
        "Give me a three step plan to learn guitar chords this week, then tell me which step is hardest.",
    ),
    ("followup", "Okay, which of those would you try first, and why?"),
    ("self_id", "Can you see me right now, or are you just listening?"),
]


def num_ok(want):
    def f(name, args):
        try:
            return abs(evaluate(args["expression"]) - want) < 1e-6 * max(1, abs(want))
        except Exception:
            return False

    return f


TOOLS = [
    (
        "weather",
        "What's the weather like in Springfield right now?",
        "get_weather",
        lambda n, ar: True,
    ),
    ("pct", "What is 17.5 percent of 2480?", "calculate", num_ok(434)),
    ("times", "What's 17 times 4839?", "calculate", num_ok(82263)),
    ("divide", "What's 15 divided by 4?", "calculate", num_ok(3.75)),
    (
        "time_tokyo",
        "What time is it in Tokyo?",
        "get_datetime",
        lambda n, ar: "tokyo" in str(ar.get("location", "")).lower(),
    ),
    (
        "btc",
        "What's Bitcoin trading at?",
        "get_quote",
        lambda n, ar: str(ar.get("symbol", "")).upper().startswith("BTC"),
    ),
    ("scores", "Who won the Giants game yesterday?", "get_scores", lambda n, ar: True),
    ("list", "List the files in my Downloads folder", "list_files", lambda n, ar: True),
]


def free_pct():
    return int(
        re.search(
            r"free percentage: (\d+)%",
            subprocess.check_output(["memory_pressure", "-Q"], text=True),
        ).group(1)
    )


release = (
    (lambda: None if jb.keep_brain_loaded() else jb.close_backend())
    if hasattr(jb, "keep_brain_loaded")
    else jb.close_backend
)
select = getattr(jt, "select_schemas", None)
rows = []


def record(**k):
    rows.append(dict(label=a.label, model=a.model, **k))
    print({x: k[x] for x in ("kind", "ttft_s", "ok") if x in k}, flush=True)


jb.close_backend() if jb._BACKEND_USED else None
import ollama

ollama.generate(model=a.model, prompt="", keep_alive=0)
time.sleep(2)
first = True
for kind, text in CHAT:
    if free_pct() < 25:
        print("STOP low memory")
        break
    if not first:
        release()
    msgs = conversation_messages(
        (HIST if kind == "followup" else []) + [{"role": "user", "content": text}]
    )
    t0 = time.perf_counter()
    ttft = None
    out = ""
    last = None
    for part in jb.llm_chat(
        messages=msgs, stream=True, options={"temperature": 0.4, "num_predict": 160}
    ):
        if ttft is None and part.message.content:
            ttft = time.perf_counter() - t0
        out += part.message.content or ""
        last = part
    tot = time.perf_counter() - t0
    ev = last.eval_count or 0
    evd = (last.eval_duration or 1) / 1e9
    record(
        kind=kind,
        cold=first,
        ttft_s=round(ttft or tot, 3),
        total_s=round(tot, 3),
        prompt_tokens=last.prompt_eval_count,
        tok_per_s=round(ev / evd, 1) if ev else 0,
        load_s=round((last.load_duration or 0) / 1e9, 3),
        ok="",
        answer=out.strip().replace("\n", " ")[:300],
    )
    first = False
for kind, text, want, check in TOOLS:
    if free_pct() < 25:
        print("STOP low memory")
        break
    release()
    schemas = select(text) if select else jt.SCHEMAS
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
    t0 = time.perf_counter()
    r = jb.llm_chat(messages=msgs, tools=schemas, options={"temperature": 0.0, "num_predict": 256})
    tot = time.perf_counter() - t0
    calls = r.message.tool_calls or []
    c = calls[0].function if calls else None
    ok = bool(c and c.name == want and check(c.name, dict(c.arguments)))
    record(
        kind="tool_" + kind,
        cold=False,
        ttft_s=round(tot, 3),
        total_s=round(tot, 3),
        prompt_tokens=getattr(r, "prompt_eval_count", ""),
        tok_per_s="",
        load_s="",
        ok=ok,
        n_schemas=len(schemas),
        answer=(f"{c.name}({dict(c.arguments)})" if c else (r.message.content or "")[:150]).replace(
            "\n", " "
        ),
    )
jb.close_backend()
out = ROOT / "diagnostics" / "runs"
out.mkdir(exist_ok=True)
fn = out / f"{a.label}__{a.model.replace(':', '_').replace('/', '_')}.csv"
keys = [
    "label",
    "model",
    "kind",
    "cold",
    "ttft_s",
    "total_s",
    "prompt_tokens",
    "tok_per_s",
    "load_s",
    "ok",
    "n_schemas",
    "answer",
]
with open(fn, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
chat = [r for r in rows if not r["kind"].startswith("tool_")]
tl = [r for r in rows if r["kind"].startswith("tool_")]


def m(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


print(
    f"SUMMARY {a.label} {a.model}: chat TTFT mean {m([r['ttft_s'] for r in chat])}s (turn1 cold {chat[0]['ttft_s']}s, "
    f"turns2+ {m([r['ttft_s'] for r in chat[1:]])}s); tool-select latency mean {m([r['ttft_s'] for r in tl])}s; "
    f"tool accuracy {sum(1 for r in tl if r['ok'])}/{len(tl)}; mean prompt tokens on tool turns {m([r['prompt_tokens'] for r in tl if r['prompt_tokens']])}"
)
