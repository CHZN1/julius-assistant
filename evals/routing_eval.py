"""Routing eval: ~100 typed prompts labelled with the expected path, scored automatically.

Paths: shortcut (a deterministic handler answers: utility/file session or the live intent router), direct (Haiku answers
with no tool), tool (Haiku calls one tool), escalate (Haiku calls escalate_to_planner).
Stage 1 replays the runtime's order (utility session, file session, intent router) in sandboxes; free and deterministic.
Stage 2 sends the remaining prompts to the cloud Haiku brain with the production system prompt and tool list; it spends a few cents.

Haiku 5.5 does not accept a temperature setting (the API rejects it), so decisions are sampled; each brain case is therefore run
--runs times (default 3) and scored by majority, and cases whose decision differs between runs are reported as varying.

Usage: python evals/routing_eval.py [--no-brain] [--only r001,r002] [--workers 6] [--runs 3]
"""

import argparse
import ast
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import common


def load_cases(only=None):
    cases = [
        json.loads(l)
        for l in (Path(__file__).parent / "routing_cases.jsonl").read_text().splitlines()
        if l.strip()
    ]
    return [c for c in cases if not only or c["id"] in only]


def system_prompt():
    """The production system prompt for the cloud profile (without the speech-style sections)."""
    import julius.brain.cloud as jc
    from julius.brain.vision_mode import vision_capability_prompt
    from julius.runtime.prompt import BASE_PROMPT

    return (
        BASE_PROMPT + vision_capability_prompt(False) + jc.CAPABILITIES_PROMPT + jc.PLANNING_PROMPT
    )


class Sandbox:
    """Fresh file area + planner state per run; nothing outside a temp directory is read or written."""

    def __init__(self):
        from julius.agents.file_agent import FileAgent
        from julius.agents.file_session import FileSession
        from julius.agents.utility_session import UtilitySession
        from julius.brain.planning import Planner

        self.dir = Path(tempfile.mkdtemp(prefix="julius_eval_")).resolve()
        home = self.dir / "home"
        for d in ("Documents", "Desktop", "Downloads"):
            (home / d).mkdir(parents=True)
        for name in (
            "budget.csv",
            "stats.csv",
            "report.pdf",
            "notes.txt",
            "photo.png",
            "script.py",
            "data.ipynb",
            "taxes.pdf",
        ):
            (home / "Downloads" / name).write_text(name)
        self.agent = FileAgent(home, self.dir / "state")
        self.files = FileSession(self.agent)
        self.utility = UtilitySession(Planner(self.dir / "planner"), "America/Los_Angeles", None)
        from julius.brain.intents import LiveIntentRouter

        self.new_router = LiveIntentRouter
        from julius.agents.code_session import CodeSession

        (self.dir / "dev" / "demo").mkdir(parents=True)
        (self.dir / "dev" / "demo" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        self.code = CodeSession(
            config={"projects_root": str(self.dir / "dev")},
            projects_root=str(self.dir / "dev"),
            sandbox=False,
        )  # stages previews only; nothing here ever starts a run


def shortcut(text, sb, ctx, turn, case_history=None):
    """What the runtime does before the brain: utility session, file session, then the live intent router."""
    out = sb.utility.handle(text, turn)
    if out is not None:
        return {
            "path": "shortcut",
            "tool": "utility",
            "args": "",
            "ok": bool(out.get("ok")),
            "detail": f"{out.get('code')}: {out.get('message', '')[:90]}",
        }
    code = sb.code.handle(text, turn)
    if code is not None:
        if sb.code.pending:
            sb.code.handle("cancel", turn)  # the eval never starts a coding task
        return {
            "path": "shortcut",
            "tool": f"code:{code.get('code')}",
            "args": "",
            "ok": bool(code.get("ok")),
            "detail": code.get("message", "")[:200],
        }
    from julius.agents.file_router import route

    kind = route(text, sb.agent.root / "Documents", pending=False, has_last=False)
    hist = case_history or []
    sb.files.suppress_idle_yes = bool(
        hist
        and hist[-1].get("role") == "assistant"
        and str(hist[-1].get("content", "")).rstrip().endswith("?")
    )
    out = sb.files.handle(text, turn)
    if out is not None:
        label = kind[0] if kind else "?"
        return {
            "path": "shortcut",
            "tool": f"files:{label}",
            "args": "",
            "ok": bool(out.get("ok")) or out.get("code") in ("preview",),
            "detail": f"{out.get('code')}: {out.get('message', '')[:200]}",
        }
    router = sb.new_router()
    for c in ctx or []:
        router.record(c["fn"], c["args"], "")
    direct = router.resolve(text)
    if direct:
        return {
            "path": "shortcut",
            "tool": direct[0],
            "args": json.dumps(direct[1]),
            "ok": True,
            "detail": "",
        }
    return None


import re

DENIAL = re.compile(
    r"\b(?:can't|cannot|can not|unable to|not able to|am not able|don't have (?:the )?(?:ability|access|a way)|isn't something i can|no way for me)\b"
)


def judge(case, got):
    e = case["expect"]
    path_ok = got["path"] in e.get("path_in", [e["path"]])
    tool_ok = True
    if "tool" in e:
        tool_ok = got.get("tool") == e["tool"]
    if "tool_in" in e:
        tool_ok = got.get("tool") in e["tool_in"]
    if "tools_all" in e:
        tool_ok = set(e["tools_all"]) <= set(
            got.get("tools") or []
        )  # parallel lookups: every needed tool called in one reply
    if e["path"] == "direct":
        tool_ok = got["path"] == "direct"
    if "path_in" in e and "tool" in e:
        tool_ok = got.get("tool") == e["tool"]
    args = (got.get("args") or "").lower()
    args_ok = all(a.lower() in args for a in e.get("args_contain", []))
    success_ok = e.get("ok") is None or got.get("ok") == e["ok"]
    detail = str(got.get("detail") or "").lower()
    if "detail_contains" in e:
        success_ok = success_ok and e["detail_contains"].lower() in detail
    if e.get("no_denial") and got["path"] == "direct" and DENIAL.search(detail):
        success_ok = False  # a capability Julius has must never be declined
    return path_ok, tool_ok, args_ok, success_ok


def brain_turn(case, brain, tracker, system):
    import julius.brain.tools as jt

    msgs = (
        [{"role": "system", "content": system}]
        + case.get("history", [])
        + [{"role": "user", "content": case["text"]}]
    )
    before, t0 = sum(tracker.today_by_tier().values()), time.perf_counter()
    try:
        r = brain.chat(msgs, tools=jt.select_schemas(case["text"]), options={"num_predict": 256})
        calls = r.message.tool_calls or []
        c = calls[0].function if calls else None
        names = [x.function.name for x in calls]
        seconds = time.perf_counter() - t0
        usd = sum(tracker.today_by_tier().values()) - before
        if c is None:
            return {
                "path": "direct",
                "tool": "",
                "args": "",
                "detail": (r.message.content or "")[:220].replace("\n", " "),
                "seconds": seconds,
                "usd": usd,
            }
        path = "escalate" if c.name == "escalate_to_planner" else "tool"
        return {
            "path": path,
            "tool": c.name,
            "tools": names,
            "args": json.dumps(dict(c.arguments)),
            "detail": "",
            "seconds": seconds,
            "usd": usd,
        }
    except Exception as exc:
        return {
            "path": "error",
            "tool": "",
            "args": "",
            "detail": f"{type(exc).__name__}: {exc}"[:120],
            "seconds": time.perf_counter() - t0,
            "usd": 0.0,
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--no-brain",
        action="store_true",
        help="deterministic stage only (free); brain cases are skipped, not failed",
    )
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--runs", type=int, default=3)
    a = ap.parse_args()
    os.environ["JULIUS_BACKEND"] = "cloud"
    os.environ["JULIUS_COSTS_FILE"] = str(
        Path(tempfile.mkdtemp(prefix="julius_eval_costs_")) / "costs.jsonl"
    )
    os.environ.setdefault("JULIUS_MEMORY_DIR", tempfile.mkdtemp(prefix="julius_eval_mem_"))
    cases = load_cases(set(a.only.split(",")) if a.only else None)
    sb = Sandbox()
    rows, pending = {}, []
    for i, case in enumerate(cases, 1):
        got = shortcut(case["text"], sb, case.get("ctx"), i, case.get("history"))
        if got:
            got.update(seconds=0.0, usd=0.0)
            rows[case["id"]] = got
        else:
            pending.append(case)
    skipped = set()
    runs = {}
    if pending and not a.no_brain:
        import julius.brain.cloud as jc

        brain, tracker, system = jc.get_brain(), jc.get_tracker(), system_prompt()
        jobs = [(c, k) for c in pending for k in range(a.runs)]
        with ThreadPoolExecutor(a.workers) as ex:
            for (case, k), got in zip(
                jobs, ex.map(lambda j: brain_turn(j[0], brain, tracker, system), jobs)
            ):
                runs.setdefault(case["id"], []).append(got)
        for case in pending:
            rs = runs[case["id"]]
            verdicts = [all(judge(case, r)) for r in rs]
            sig = lambda r: (r["path"], r.get("tool", ""))
            majority_ok = sum(verdicts) * 2 > len(rs)
            pick = next(
                (r for r, v in zip(rs, verdicts) if v == majority_ok), rs[0]
            )  # a run on the majority side represents the case
            rows[case["id"]] = dict(
                pick,
                seconds=sum(r["seconds"] for r in rs) / len(rs),
                usd=sum(r["usd"] for r in rs),
                votes_ok=f"{sum(verdicts)}/{len(rs)}",
                varies=len({sig(r) for r in rs}) > 1,
                alt="; ".join(sorted({f"{sig(r)[0]}/{sig(r)[1]}" for r in rs})),
            )
    else:  # deterministic-only run: a missing shortcut is still a failure; brain-dependent cases are skipped
        for case in pending:
            if case["expect"]["path"] == "shortcut" and "path_in" not in case["expect"]:
                rows[case["id"]] = {
                    "path": "fell_through_to_brain",
                    "tool": "",
                    "args": "",
                    "detail": "no shortcut matched",
                    "seconds": 0.0,
                    "usd": 0.0,
                }
            else:
                skipped.add(case["id"])
    out = []
    for case in cases:
        e = case["expect"]
        if case["id"] in skipped:
            continue
        got = rows[case["id"]]
        p, t, ar, s = judge(case, got)
        out.append(
            {
                "id": case["id"],
                "cat": case["cat"],
                "text": case["text"],
                "expected_path": e["path"],
                "expected_tool": e.get("tool")
                or "|".join(e.get("tool_in", []))
                or "+".join(e.get("tools_all", [])),
                "actual_path": got["path"],
                "actual_tool": got.get("tool", ""),
                "args": got.get("args", ""),
                "path_ok": p,
                "tool_ok": t,
                "args_ok": ar,
                "ok": (p and t and ar and s)
                if "votes_ok" not in got
                else int(got["votes_ok"].split("/")[0]) * 2 > int(got["votes_ok"].split("/")[1]),
                "votes_ok": got.get("votes_ok", ""),
                "varies": got.get("varies", False),
                "variants": got.get("alt", ""),
                "seconds": round(got.get("seconds", 0), 2),
                "usd": round(got.get("usd", 0), 6),
                "detail": got.get("detail", ""),
            }
        )
    n = len(out)
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
    by = lambda key: {
        k: {
            "n": sum(1 for r in out if r[key] == k),
            "ok_pct": pct([r["ok"] for r in out if r[key] == k]),
            "path_pct": pct([r["path_ok"] for r in out if r[key] == k]),
        }
        for k in sorted({r[key] for r in out})
    }
    brain_rows = [r for r in out if r["seconds"] > 0]
    summary = {
        "suite": "routing",
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "cases": n,
        "skipped_brain_cases": len(skipped),
        "ok_pct": pct([r["ok"] for r in out]),
        "path_ok_pct": pct([r["path_ok"] for r in out]),
        "tool_ok_pct": pct([r["tool_ok"] for r in out]),
        "by_category": by("cat"),
        "by_expected_path": by("expected_path"),
        "varying_cases": sum(1 for r in out if r["varies"]),
        "runs_per_brain_case": a.runs if not a.no_brain else 0,
        "brain_mean_seconds": round(sum(r["seconds"] for r in brain_rows) / len(brain_rows), 2)
        if brain_rows
        else None,
        "total_usd": round(sum(r["usd"] for r in out), 4),
        "confusion": {f"{r['expected_path']}->{r['actual_path']}": 0 for r in out},
    }
    for r in out:
        summary["confusion"][f"{r['expected_path']}->{r['actual_path']}"] += 1
    path, previous = common.write_results(
        "partial_routing" if (a.only or a.no_brain) else "routing", out, summary
    )
    broke, fixed, vs = common.regressions(out, previous)
    summary["regressions_vs_previous"] = {
        "previous": vs,
        "newly_failing": broke,
        "newly_passing": fixed,
    }
    path.with_suffix(".summary.json").write_text(json.dumps(summary, indent=1))
    print(
        f"routing: {summary['varying_cases']} of {sum(1 for r in out if r['votes_ok'])} brain cases vary between runs; {summary['ok_pct']}% correct ({sum(r['ok'] for r in out)}/{n}); path {summary['path_ok_pct']}%, tool {summary['tool_ok_pct']}%; ${summary['total_usd']}; results {path.name}"
    )
    for k, v in summary["by_category"].items():
        print(f"  {k:9} n={v['n']:3} ok={v['ok_pct']}% path={v['path_pct']}%")
    print("  confusion:", {k: v for k, v in sorted(summary["confusion"].items())})
    for r in out:
        if not r["ok"]:
            print(
                f"  MISS {r['id']} [{r['cat']}] {r['text'][:70]!r}: expected {r['expected_path']}/{r['expected_tool']} got {r['actual_path']}/{r['actual_tool']} {r['args'][:60]} {r['detail'][:60]}"
            )
    if broke:
        print(f"  REGRESSIONS vs {vs}: {broke}")
    elif previous:
        print(f"  no regressions vs {vs}" + (f"; newly passing: {fixed}" if fixed else ""))
    return 1 if broke else 0


if __name__ == "__main__":
    sys.exit(main())
