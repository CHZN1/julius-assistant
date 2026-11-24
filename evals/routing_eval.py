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
        json.loads(line)
        for line in (Path(__file__).parent / "routing_cases.jsonl").read_text().splitlines()
        if line.strip()
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
    from julius.music import commands as music_commands
    from julius.music import convert as music_convert

    if (
        music_convert.parse(text) is not None
    ):  # a convert plan through the file agent, before the brain
        return {
            "path": "shortcut",
            "tool": "convert_audio",
            "args": "",
            "ok": True,
            "detail": "convert",
        }
    from julius.art import camera as art_camera
    from julius.art import commands as art_commands

    if (
        art_camera.parse(text) is not None
    ):  # take a photo / selfie: a fixed pattern, before the brain
        return {
            "path": "shortcut",
            "tool": "take_photo",
            "args": "",
            "ok": True,
            "detail": "camera",
        }
    from julius.conversations import commands as conv_commands

    if conv_commands.is_command(
        text
    ):  # new / open / rename / move a conversation: fixed patterns, before the brain
        return {
            "path": "shortcut",
            "tool": "conversations",
            "args": "",
            "ok": True,
            "detail": "conversation",
        }
    from julius.art import tool as art_tool
    from julius.ui_bridge import panel_emit as pe

    if (
        pe.SAVE_RE.match(text)
        or pe.SAVE_WHAT_RE.match(text)
        or (
            (pe.SAVE_AS_RE.match(text) or pe.CALL_IT_RE.match(text))
            and not art_tool.SET_MAIN.match(text)
        )
    ):  # "save this card", "save as Roy's main sprite", "call it Bob": the panel commands, before the brain
        return {"path": "shortcut", "tool": "save_card", "args": "", "ok": True, "detail": "save"}
    if art_tool.SET_MAIN.match(text):  # "make this my main sprite": before the brain
        return {
            "path": "shortcut",
            "tool": "sprite_edit",
            "args": "main",
            "ok": True,
            "detail": "main sprite",
        }
    from julius.runtime import heard

    last = next(
        (m["content"] for m in reversed(case_history or []) if m.get("role") == "assistant"), ""
    )
    if art_commands.PALETTE_WORD.match(text) and heard.expects_palette(
        last
    ):  # "Nes" after a palette question: before the brain
        return {
            "path": "shortcut",
            "tool": "image_style",
            "args": "sprite " + text.lower(),
            "ok": True,
            "detail": "palette answer",
        }
    low = last.lower()
    if (
        text.strip()
        and art_commands._YES_OPEN.match(text)
        and "?" in low
        and "open" in low
        and "sprite" in low
    ):
        return {
            "path": "shortcut",
            "tool": "sprite_edit",
            "args": "open",
            "ok": True,
            "detail": "answer to open",
        }
    if art_commands.OPEN_SPRITE.match(text) and re.search(r"\bsprite\b", text, re.I):
        return {  # "open my sprite": handled before the brain
            "path": "shortcut",
            "tool": "sprite_edit",
            "args": "",
            "ok": True,
            "detail": "open sprite",
        }
    if art_commands.SPRITE_BUTTON.match(
        text
    ):  # a sprite card's edit button: handled before the brain
        return {
            "path": "shortcut",
            "tool": "sprite_edit",
            "args": "",
            "ok": True,
            "detail": "sprite button",
        }
    if art_commands.parse(text) is not None:  # pixel art: a fixed pattern, before the brain
        return {
            "path": "shortcut",
            "tool": "pixel_art",
            "args": "",
            "ok": True,
            "detail": "pixel art",
        }
    if music_commands.is_rescan(text):  # answered before the brain by a fixed pattern
        return {
            "path": "shortcut",
            "tool": "rescan_samples",
            "args": "",
            "ok": True,
            "detail": "rescan",
        }
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


def arg_matches(expected, args_text):
    """True when the tool arguments contain `expected`, or a company or coin name the price tool resolves to it
    (get_quote takes "Tesla" as well as "TSLA": the tool turns the name into the ticker)."""
    wanted = expected.lower()
    if wanted in args_text:
        return True
    from julius.brain import tickers

    try:
        values = [str(v) for v in json.loads(args_text).values()]
    except (ValueError, AttributeError):
        return False
    for value in values:
        found = tickers.resolve(value, search=lambda _name: [])
        if found.symbol and found.symbol.split("-")[0].lower() == wanted:
            return True
    return False


CANNED = {
    "get_quote_history": "Apple Inc. (AAPL) daily closing prices, last 5 trading days (USD):\n2026-11-05: 268.47\n2026-11-06: 269.77\n2026-11-07: 271.49\n2026-11-10: 273.40\n2026-11-11: 275.25\nSource: Yahoo Finance; quotes may be delayed.",
    "research_task": "Iceland has about 380,000 people, Norway 5.5 million and Denmark 5.9 million. [research: 3 cited finding(s)]",
    "render_chart": "Chart shown in the side panel.",
    "get_weather": "Springfield: 46°F, clear, feels like 37. High 47, low 26.",
    "get_quote": "Bitcoin USD (BTC-USD): 68,183.01 USD, down 4.43% versus previous close. Source: Yahoo Finance; quotes may be delayed.",
    "get_scores": "NHL (today) — 2 games:\nSCHEDULED: New York Rangers at Vancouver Canucks, 9:00 PM EST\nSCHEDULED: Nashville Predators at Vegas Golden Knights, 10:00 PM EST",
    "get_news": "News search results (headlines and snippets, articles not verified):\nCity council approves new budget — Local Times (2026-11-13)",
}


def in_order(chain, seq):
    """True when `chain` appears in `seq` as an ordered subsequence."""
    it = iter(seq)
    return all(name in it for name in chain)


def chain_turn(case, brain, system, rounds=3):
    """A multi-round case: the model's tool calls are answered with canned results until it stops calling tools."""
    import julius.brain.tools as jt

    msgs = (
        [{"role": "system", "content": system}]
        + (
            [{"role": "system", "content": case["state"]}] if case.get("state") else []
        )  # SESSION STATE, as production adds it
        + case.get("history", [])
        + [{"role": "user", "content": case["text"]}]
    )
    seq, args, final = [], [], ""
    for round_no in range(rounds):
        options = {"num_predict": 256}
        if round_no == 0:  # the runtime forces the first call for these requests
            options["force_research"] = jt.research_required(case["text"])
            if jt.calculation_required(case["text"]):
                options["force_name"] = "calculate"
        r = brain.chat(msgs, tools=jt.select_schemas(case["text"]), options=options)
        calls = r.message.tool_calls or []
        if not calls:
            final = r.message.content or ""
            break
        msgs.append(
            {
                "role": "assistant",
                "content": r.message.content or "",
                "tool_calls": [
                    {"function": {"name": c.function.name, "arguments": dict(c.function.arguments)}}
                    for c in calls
                ],
            }
        )
        for c in calls:
            seq.append(c.function.name)
            args.append(json.dumps(dict(c.function.arguments)))
            msgs.append(
                {
                    "role": "tool",
                    "name": c.function.name,
                    "content": tool_result(c.function.name, dict(c.function.arguments)),
                }
            )
    return seq, args, final


def tool_result(name, args):
    """What a tool says in a chain case: the real calculator (free and deterministic), canned text for the rest."""
    if name == "calculate":
        import julius.brain.tools as jt

        try:
            return str(jt.call("calculate", args))
        except Exception as e:
            return f"calculate failed: {e}"
    if name == "get_quote" and args.get("days"):
        return CANNED["get_quote_history"]
    return CANNED.get(name, "Done.")


CLARIFY = re.compile(
    r"\b(?:which|where|what (?:state|city|place|location)|do you mean|ambiguous|several|many|more than one|specify)\b"
)


def clarifies(got):
    """A direct reply that asks where: correct for a place name that many towns share."""
    detail = str(got.get("detail") or "").lower()
    return got["path"] == "direct" and "?" in detail and bool(CLARIFY.search(detail))


def said_all(got: dict) -> str:
    return " ".join(str(got.get(k) or "") for k in ("final", "detail")).split(" | ")[-1]


def judge(case, got):
    e = case["expect"]
    if (
        "any_of" in e
    ):  # several acceptable outcomes (the planner, or a direct chain that calls every needed tool)
        tries = [judge({**case, "expect": alt}, got) for alt in e["any_of"]]
        return next((t for t in tries if all(t)), tries[0])
    if e.get("clarify_ok") and clarifies(got):
        return True, True, True, True
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
    if "chain_all" in e:  # every needed tool called within the round cap, in any order
        tool_ok = set(e["chain_all"]) <= set(got.get("seq") or [])
    if "chain" in e:
        tool_ok = in_order(e["chain"], got.get("seq") or [])
    if (
        "answer_contains" in e and "final" in got
    ):  # the model called the right tool(s) AND its final answer states the correct result (a deterministic shortcut has no model answer)
        tool_ok = (
            tool_ok
            and in_order(e.get("chain", []), got.get("seq") or [])
            and all(re.search(p, (got.get("final") or "").lower()) for p in e["answer_contains"])
        )
    if e["path"] == "direct":
        tool_ok = got["path"] in e.get("path_in", ["direct"])
    if "path_in" in e and "tool" in e:
        tool_ok = got.get("tool") == e["tool"]
    args = (got.get("args") or "").lower()
    args_ok = all(arg_matches(a, args) for a in e.get("args_contain", []))
    success_ok = e.get("ok") is None or got.get("ok") == e["ok"]
    detail = str(got.get("detail") or "").lower()
    if "detail_contains" in e:
        success_ok = success_ok and e["detail_contains"].lower() in detail
    if e.get("no_denial") and got["path"] == "direct" and DENIAL.search(detail):
        success_ok = False  # a capability Julius has must never be declined
    for pat in e.get(
        "answer_not", []
    ):  # e.g. a denial of something Julius did ("I haven't made any sprites")
        if re.search(pat, said_all(got), re.I):
            success_ok = False
    from julius.runtime.text import is_narration

    said = str(got.get("final") or "")
    if not said and got["path"] in ("direct", "tool"):
        said = str(got.get("detail") or "")
    said = said.split(" | ")[
        -1
    ]  # "get_quote -> render_chart | <answer>": only the answer is ever spoken
    if any(is_narration(x) for x in re.split(r"(?<=[.!?])\s+", said) if x):
        success_ok = False  # every case: a spoken reply never narrates a tool call ("I'll call the sprite tool now")
    return path_ok, tool_ok, args_ok, success_ok


def brain_turn(case, brain, tracker, system):
    import julius.brain.tools as jt

    msgs = (
        [{"role": "system", "content": system}]
        + case.get("history", [])
        + [
            {
                "role": "user",
                "content": case["text"] + ("\n\n" + case["remind"] if case.get("remind") else ""),
            }
        ]
    )
    before, t0 = sum(tracker.today_by_tier().values()), time.perf_counter()
    try:
        options = {
            "num_predict": 256,
            "force_planner": jt.planner_required(case["text"])
            and not jt.research_required(case["text"]),
            "force_research": jt.research_required(case["text"]),
            "force_name": "calculate" if jt.calculation_required(case["text"]) else "",
            "force_tool": jt.weekday_question(case["text"]),
        }
        r = brain.chat(msgs, tools=jt.select_schemas(case["text"]), options=options)
        calls = r.message.tool_calls or []
        if any(k in case["expect"] for k in ("chain", "chain_all", "answer_contains")) or any(
            "chain_all" in alt for alt in case["expect"].get("any_of", [])
        ):
            seq, args, final = chain_turn(case, brain, system)
            usd = sum(tracker.today_by_tier().values()) - before
            return {
                "path": ("escalate" if seq[0] == "escalate_to_planner" else "tool")
                if seq
                else "direct",
                "tool": seq[0] if seq else "",
                "seq": seq,
                "args": " ".join(args),
                "detail": " -> ".join(seq) + (" | " + final if final else ""),
                "final": final,
                "seconds": time.perf_counter() - t0,
                "usd": usd,
            }
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
            "seq": names,
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


def run_shortcuts(cases):
    """Try the deterministic stage on every case: (rows for those it answered, cases left over)."""
    sb = Sandbox()
    rows, pending = {}, []
    for i, case in enumerate(cases, 1):
        got = shortcut(case["text"], sb, case.get("ctx"), i, case.get("history"))
        if got:
            got.update(seconds=0.0, usd=0.0)
            rows[case["id"]] = got
        else:
            pending.append(case)
    return rows, pending


def run_brain(pending, runs, workers):
    """Run every pending case through the paid brain several times; one representative row per case."""
    import julius.brain.cloud as jc

    brain, tracker, system = jc.get_brain(), jc.get_tracker(), system_prompt()
    jobs = [(c, k) for c in pending for k in range(runs)]
    by_case = {}
    with ThreadPoolExecutor(workers) as ex:
        for (case, _k), got in zip(
            jobs, ex.map(lambda j: brain_turn(j[0], brain, tracker, system), jobs), strict=False
        ):
            by_case.setdefault(case["id"], []).append(got)
    rows = {}
    for case in pending:
        rs = by_case[case["id"]]
        verdicts = [all(judge(case, r)) for r in rs]

        def sig(r):
            return (r["path"], r.get("tool", ""))

        majority_ok = sum(verdicts) * 2 > len(rs)
        # a run on the majority side represents the case
        pick = next((r for r, v in zip(rs, verdicts, strict=False) if v == majority_ok), rs[0])
        rows[case["id"]] = dict(
            pick,
            seconds=sum(r["seconds"] for r in rs) / len(rs),
            usd=sum(r["usd"] for r in rs),
            votes_ok=f"{sum(verdicts)}/{len(rs)}",
            varies=len({sig(r) for r in rs}) > 1,
            alt="; ".join(sorted({f"{sig(r)[0]}/{sig(r)[1]}" for r in rs})),
        )
    return rows


def without_brain(pending):
    """Deterministic-only run: a missing shortcut is still a failure; brain cases are skipped."""
    rows, skipped = {}, set()
    for case in pending:
        if case["expect"].get("path") == "shortcut" and "path_in" not in case["expect"]:
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
    return rows, skipped


def result_row(case, got):
    """The CSV row for one judged case."""
    e = case["expect"]
    p, t, ar, s = judge(case, got)
    if "votes_ok" in got:
        yes, total = got["votes_ok"].split("/")
        ok = int(yes) * 2 > int(total)
    else:
        ok = p and t and ar and s
    return {
        "id": case["id"],
        "cat": case["cat"],
        "text": case["text"],
        "expected_path": e.get("path") or "|".join(a.get("path", "") for a in e.get("any_of", [])),
        "expected_tool": e.get("tool")
        or "|".join(e.get("tool_in", []))
        or "+".join(e.get("tools_all", [])),
        "actual_path": got["path"],
        "actual_tool": got.get("tool", ""),
        "args": got.get("args", ""),
        "path_ok": p,
        "tool_ok": t,
        "args_ok": ar,
        "ok": ok,
        "votes_ok": got.get("votes_ok", ""),
        "varies": got.get("varies", False),
        "variants": got.get("alt", ""),
        "seconds": round(got.get("seconds", 0), 2),
        "usd": round(got.get("usd", 0), 6),
        "detail": got.get("detail", ""),
    }


def _pct(xs):
    return round(100 * sum(xs) / len(xs), 1) if xs else None


def _breakdown(out, key):
    return {
        k: {
            "n": sum(1 for r in out if r[key] == k),
            "ok_pct": _pct([r["ok"] for r in out if r[key] == k]),
            "path_pct": _pct([r["path_ok"] for r in out if r[key] == k]),
        }
        for k in sorted({r[key] for r in out})
    }


def summarize(out, skipped, runs):
    """Aggregate accuracy, variation, latency and cost over the result rows."""
    brain_rows = [r for r in out if r["seconds"] > 0]
    summary = {
        "suite": "routing",
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "cases": len(out),
        "skipped_brain_cases": len(skipped),
        "ok_pct": _pct([r["ok"] for r in out]),
        "path_ok_pct": _pct([r["path_ok"] for r in out]),
        "tool_ok_pct": _pct([r["tool_ok"] for r in out]),
        "by_category": _breakdown(out, "cat"),
        "by_expected_path": _breakdown(out, "expected_path"),
        "varying_cases": sum(1 for r in out if r["varies"]),
        "runs_per_brain_case": runs,
        "brain_mean_seconds": round(sum(r["seconds"] for r in brain_rows) / len(brain_rows), 2)
        if brain_rows
        else None,
        "total_usd": round(sum(r["usd"] for r in out), 4),
        "confusion": {f"{r['expected_path']}->{r['actual_path']}": 0 for r in out},
    }
    for r in out:
        summary["confusion"][f"{r['expected_path']}->{r['actual_path']}"] += 1
    return summary


def print_report(out, summary, path, broke, fixed, vs, previous):
    n = len(out)
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
    os.environ["JULIUS_LOCAL_CONFIG"] = str(
        Path(__file__).parent / "routing_profile.toml"
    )  # fixed test name and home city: "will it rain tomorrow?" always means Denver here
    os.environ["JULIUS_COSTS_FILE"] = str(
        Path(tempfile.mkdtemp(prefix="julius_eval_costs_")) / "costs.jsonl"
    )
    os.environ.setdefault("JULIUS_MEMORY_DIR", tempfile.mkdtemp(prefix="julius_eval_mem_"))
    cases = load_cases(set(a.only.split(",")) if a.only else None)
    rows, pending = run_shortcuts(cases)
    skipped = set()
    if pending and not a.no_brain:
        rows.update(run_brain(pending, a.runs, a.workers))
    else:
        extra, skipped = without_brain(pending)
        rows.update(extra)
    out = [result_row(c, rows[c["id"]]) for c in cases if c["id"] not in skipped]
    summary = summarize(out, skipped, a.runs if not a.no_brain else 0)
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
    print_report(out, summary, path, broke, fixed, vs, previous)
    return 1 if broke else 0


if __name__ == "__main__":
    sys.exit(main())
