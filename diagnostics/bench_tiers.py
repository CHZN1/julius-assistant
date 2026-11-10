"""Per-tier benchmark against the live API: Haiku front end (latency, tool accuracy, caching, cost) and the
Sonnet planner (end-to-end escalation with a sandboxed file area). Spends real money (cents).

Usage: JULIUS_BACKEND=cloud python diagnostics/bench_tiers.py
"""

import ast
import csv
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["JULIUS_BACKEND"] = "cloud"
sys.path.insert(0, str(ROOT))
import julius_cloud as jc
import julius_orchestrator as orch
import julius_tools as jt
from julius_calculator import evaluate
from julius_conversation import conversation_messages
from julius_dispatcher import Dispatcher
from julius_file_agent import FileAgent
from julius_file_session import FileSession


def candidate_system():
    src = ast.parse((ROOT / "julius_candidate.py").read_text())
    ns = {}
    for n in src.body:
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SYSTEM" for t in n.targets
        ):
            exec(compile(ast.Module(body=[n], type_ignores=[]), "<c>", "exec"), ns)
    from julius_vision_mode import vision_capability_prompt

    return ns["SYSTEM"] + vision_capability_prompt(False) + jc.PLANNING_PROMPT


SYSTEM = candidate_system()
brain = jc.get_brain()
tracker = jc.get_tracker()
rows = []


def spent():
    return sum(tracker.today_by_tier().values())


def num_ok(want):
    return lambda a: abs(evaluate(a["expression"]) - want) < 1e-6 * max(1, abs(want))


CHAT = [
    ("chitchat", "Hey Julius, how's it going?"),
    ("factual", "What's the difference between overfitting and underfitting?"),
    (
        "multistep_chat",
        "Give me a three step plan to learn guitar chords this week, then tell me which step is hardest.",
    ),
    ("followup", "Okay, which of those would you try first, and why?"),
    ("self_id", "Can you see me right now, or are you just listening?"),
]
HIST = [
    {
        "role": "user",
        "content": "My CNN gets 62 percent validation accuracy while training hits 95.",
    },
    {
        "role": "assistant",
        "content": "That gap is classic overfitting. Try augmentation, dropout and early stopping.",
    },
]
TOOLS = [
    ("weather", "What's the weather like in Springfield right now?", "get_weather", lambda a: True),
    ("pct", "What is 17.5 percent of 2480?", "calculate", num_ok(434)),
    ("times", "What's 17 times 4839?", "calculate", num_ok(82263)),
    (
        "time_tokyo",
        "What time is it in Tokyo?",
        "get_datetime",
        lambda a: "tokyo" in str(a.get("location", "")).lower(),
    ),
    (
        "btc",
        "What's Bitcoin trading at?",
        "get_quote",
        lambda a: str(a.get("symbol", "")).upper().startswith("BTC"),
    ),
    ("scores", "Who won the Giants game yesterday?", "get_scores", lambda a: True),
    ("list", "List the files in my Downloads folder", "list_files", lambda a: True),
]
ESCALATE = [
    ("organize", "Organize my downloads folder by file type.", {"files_propose"}),
    (
        "trip",
        "Plan a three day trip to Tokyo for me, including what the weather is like and what time it is there right now.",
        {"get_weather", "get_datetime"},
    ),
    (
        "compare",
        "Compare today's weather in Springfield and in Tokyo and tell me which is warmer.",
        {"get_weather"},
    ),
]

# ---------------- Haiku front end ----------------
print("== Haiku front end", flush=True)
for kind, text in CHAT:
    msgs = conversation_messages(
        (HIST if kind == "followup" else []) + [{"role": "user", "content": text}]
    )
    before, t0, ttft, out = spent(), time.perf_counter(), None, ""
    for part in brain.chat(msgs, stream=True, options={"num_predict": 160}):
        if ttft is None and part.message.content:
            ttft = time.perf_counter() - t0
        out += part.message.content
    rows.append(
        dict(
            tier="haiku",
            kind="chat_" + kind,
            ttft_s=round(ttft or 0, 2),
            total_s=round(time.perf_counter() - t0, 2),
            ok="",
            usd=round(spent() - before, 6),
            detail=out.strip().replace("\n", " ")[:140],
        )
    )
    print(rows[-1], flush=True)


def haiku_tool(kind, text, want, check):
    before, t0 = spent(), time.perf_counter()
    r = brain.chat(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
        tools=jt.select_schemas(text),
        options={"num_predict": 256},
    )
    total = time.perf_counter() - t0
    calls = r.message.tool_calls or []
    c = calls[0].function if calls else None
    ok = bool(c and c.name == want and check(dict(c.arguments)))
    rows.append(
        dict(
            tier="haiku",
            kind="tool_" + kind,
            ttft_s=round(total, 2),
            total_s=round(total, 2),
            ok=ok,
            usd=round(spent() - before, 6),
            detail=(f"{c.name}({dict(c.arguments)})" if c else (r.message.content or "")[:100]),
        )
    )
    print(rows[-1], flush=True)


for kind, text, want, check in TOOLS:
    haiku_tool(kind, text, want, check)
for kind, text, expected in ESCALATE:
    haiku_tool(
        "escalate_" + kind,
        text,
        "escalate_to_planner",
        lambda a: bool(str(a.get("task", "")).strip()),
    )

# ---------------- Sonnet planner end-to-end (sandboxed files) ----------------
print("== Sonnet planner", flush=True)
sandbox = Path(tempfile.mkdtemp()).resolve()
home = sandbox / "home"
for d in ("Documents", "Desktop", "Downloads"):
    (home / d).mkdir(parents=True)
for name in (
    "budget.csv",
    "stats.csv",
    "report.pdf",
    "notes.pdf",
    "photo.png",
    "script.py",
    "readme.txt",
):
    (home / "Downloads" / name).write_text(name)
agent = FileAgent(home, sandbox / "state")
session = FileSession(agent)
orch.configure(file_session=session, turn=lambda: 1)
dispatcher = Dispatcher(jt.REGISTRY, jt.SCHEMAS, agent)
jt._dispatcher = dispatcher
executed = []
real_call = jt.call


def spy(name, args):
    executed.append(name)
    return real_call(name, args)


jt.call = spy
orig_execute = orch.Planner.execute


def spy_execute(self, name, args):
    executed.append("planner:" + name)
    if name == "files_propose":
        executed.append("files_propose")
    return orig_execute(self, name, args)


orch.Planner.execute = spy_execute
for kind, text, expected in ESCALATE:
    executed.clear()
    before = dict(tracker.today_by_tier())
    b_total = spent()
    t0 = time.perf_counter()
    planner_holder = {}
    orig_run = orch.Planner.run

    def run(self, task, _orig=orig_run):
        planner_holder["p"] = self
        return _orig(self, task)

    orch.Planner.run = run
    answer = orch.run_escalation({"task": text}, dispatcher, brain=brain)
    orch.Planner.run = orig_run
    total = time.perf_counter() - t0
    p = planner_holder.get("p")
    after = tracker.today_by_tier()
    tiers = {k: round(after.get(k, 0) - before.get(k, 0), 6) for k in after}
    used = {n for n in executed if not n.startswith("planner:")} | {
        n.split(":", 1)[1] for n in executed if n.startswith("planner:")
    }
    ok = expected <= used
    rows.append(
        dict(
            tier="sonnet",
            kind="plan_" + kind,
            ttft_s="",
            total_s=round(total, 2),
            ok=ok,
            usd=round(spent() - b_total, 6),
            detail=f"steps={getattr(p, 'steps', '?')} subagents={len(getattr(p, 'subagent_runs', []))} cost={tiers} tools={sorted(used)} -> {answer[:160]}",
        )
    )
    print(rows[-1], flush=True)
print(
    "pending file plan staged for user, files untouched:",
    bool(session.pending),
    sorted(x.name for x in (home / "Downloads").iterdir()),
)
jt.call = real_call

# ---------------- summary ----------------
out = ROOT / "diagnostics" / "tier_benchmarks.csv"
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["tier", "kind", "ttft_s", "total_s", "ok", "usd", "detail"])
    w.writeheader()
    w.writerows(rows)
m = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
h_chat = [r for r in rows if r["kind"].startswith("chat_")]
h_tool = [r for r in rows if r["kind"].startswith("tool_")]
h_esc = [r for r in rows if r["kind"].startswith("tool_escalate_")]
h_plain = [r for r in h_tool if not r["kind"].startswith("tool_escalate_")]
s_rows = [r for r in rows if r["tier"] == "sonnet"]
print(
    f"SUMMARY haiku chat TTFT mean {m([r['ttft_s'] for r in h_chat])}s (first {h_chat[0]['ttft_s']}s, rest {m([r['ttft_s'] for r in h_chat[1:]])}s); "
    f"tool call latency mean {m([r['total_s'] for r in h_plain])}s; tool accuracy {sum(1 for r in h_plain if r['ok'])}/{len(h_plain)}; "
    f"escalation routing {sum(1 for r in h_esc if r['ok'])}/{len(h_esc)}; "
    f"sonnet planner mean {m([r['total_s'] for r in s_rows])}s, tool-use correct {sum(1 for r in s_rows if r['ok'])}/{len(s_rows)}; "
    f"benchmark spend ${sum(r['usd'] for r in rows):.4f}; today ${tracker.today_total():.4f}"
)
