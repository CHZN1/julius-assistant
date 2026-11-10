"""Benchmark MLX language models for speed on Julius's prompt shapes."""

import csv
import os
import sys
import time

os.environ["JULIUS_BACKEND"] = "mlx"
os.environ["JULIUS_MODEL"] = str(
    __import__("pathlib").Path(__file__).resolve().parent.parent / ".cache/julius-mlx-brain"
)
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import julius_backend as jb
import julius_tools as jt
from julius_conversation import conversation_messages

sys.path.insert(0, os.path.dirname(__file__))
import mlx.core as mx
from bench_brain import FOLLOW, HIST, PROMPTS, free_pct

rows = []
for i, (kind, text) in enumerate(PROMPTS):
    if free_pct() < 25:
        print("STOP low mem")
        break
    msgs = (
        (HIST + [{"role": "user", "content": FOLLOW}])
        if kind == "followup"
        else [{"role": "user", "content": text}]
    )
    msgs = conversation_messages(msgs)
    tools = jt.SCHEMAS if kind.startswith("tool") else None
    t0 = time.perf_counter()
    ttft = None
    out = ""
    if tools:
        r = jb.llm_chat(
            messages=msgs, tools=tools, options={"temperature": 0.4, "num_predict": 160}
        )
        ttft = time.perf_counter() - t0
        out = (
            (r.message.content or "")
            + " [TOOL_CALL "
            + ",".join(
                f"{c.function.name}({dict(c.function.arguments)})" for c in r.message.tool_calls
            )
            + "]"
            if r.message.tool_calls
            else r.message.content
        )
    else:
        for p in jb.llm_chat(
            messages=msgs, stream=True, options={"temperature": 0.4, "num_predict": 160}
        ):
            if ttft is None:
                ttft = time.perf_counter() - t0
            out += p.message.content
    tot = time.perf_counter() - t0
    m = jb.generation_metrics()
    rows.append(
        dict(
            model="mlx qwen3-4b-instruct 4bit",
            idx=i,
            kind=kind,
            cold=(i == 0),
            load_s="",
            prompt_tokens=m.get("prompt_tokens"),
            prompt_eval_s="",
            ttft_s=round(ttft, 3),
            gen_tokens=m.get("generation_tokens"),
            tok_per_s=round(m.get("generation_tps", 0), 1),
            total_s=round(tot, 3),
            resident_gb=round(mx.get_peak_memory() / 1e9, 2),
            gpu_gb=round(mx.get_peak_memory() / 1e9, 2),
            free_pct=free_pct(),
            answer=(out or "").strip().replace("\n", " ")[:400],
        )
    )
    print(
        rows[-1]["kind"],
        rows[-1]["ttft_s"],
        rows[-1]["tok_per_s"],
        rows[-1]["total_s"],
        rows[-1]["resident_gb"],
        flush=True,
    )
jb.close_backend()
print("active after release", mx.get_active_memory())
with open(
    str(__import__("pathlib").Path(__file__).resolve().parent / "benchmarks.csv"), "a", newline=""
) as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writerows(rows)
