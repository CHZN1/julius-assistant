"""Routing shake test: are the brain's decisions robust, or do they sit on a coin flip?

The cloud brain is sent the whole tool list and one long system prompt on every turn, so a case with no decisive rule can flip when anything in that prefix changes:
a new tool, a longer description, a different tool order, more prompt text. (This is what broke r101 and r129 every time a tool was added.) This test applies
harmless changes of exactly that kind and runs the cases under each of them. A case that passes only under some of them is fragile: it needs a decisive tie-break
rule (in the system prompt or the competing tool descriptions), not a pattern trigger and not luck.

  python evals/routing_shake.py                  known-fragile cases only (cases marked "fragile": true in routing_cases.jsonl), 7 perturbations x 3 runs; about $0.30
  python evals/routing_shake.py --mode full      every brain case, 4 perturbations x 1 run; about $2
  python evals/routing_shake.py --only r101,r129 --runs 5 --perturbations base,long_tool

Exit status 1 when any case passes in fewer than --threshold (default 0.8) of its runs, so it can gate a change. Run it whenever a tool is added or a tool
description or the system prompt is edited, before routing x3. Pass counts are over (perturbation x run) for each case; the "base" column is the unperturbed setup.
Writes evals/results/shake_<time>.csv like the other evals."""

from __future__ import annotations

import argparse
import copy
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

# the same fixed profile and scratch state as routing_eval.main, set before any julius import
os.environ["JULIUS_BACKEND"] = "cloud"
os.environ["JULIUS_LOCAL_CONFIG"] = str(HERE / "routing_profile.toml")
os.environ["JULIUS_COSTS_FILE"] = str(
    Path(tempfile.mkdtemp(prefix="julius_shake_costs_")) / "costs.jsonl"
)
os.environ.setdefault("JULIUS_MEMORY_DIR", tempfile.mkdtemp(prefix="julius_shake_mem_"))

import common  # noqa: E402
import routing_eval as re_  # noqa: E402

DUMMY_SHORT = "Set the display brightness."
DUMMY_LONG = (
    "Set the display brightness from 0 to 100 percent. Use when the user asks to make the screen brighter, dimmer, darker or lighter, or names a brightness level. "
    "Never for volume, never for the keyboard backlight, and never invent a level the user did not say. Returns the new level so it can be said back briefly."
)
PAD = "\n\nNote on style. " + (
    "The weather is a common small talk topic and people enjoy hearing about it in a friendly way. "
    * 25
)


def _dummy(description: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "set_screen_brightness",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"level": {"type": "integer"}},
                "required": ["level"],
            },
        },
    }


# name -> (what changes in the tool list, what is added to the system prompt)
PERTURBATIONS = {
    "base": (lambda t: t, ""),
    "short_tool": (lambda t: t + [_dummy(DUMMY_SHORT)], ""),
    "long_tool": (lambda t: t + [_dummy(DUMMY_LONG)], ""),
    "tool_first": (lambda t: [_dummy(DUMMY_LONG)] + t, ""),
    "reversed": (lambda t: list(reversed(t)), ""),
    "padded_prompt": (lambda t: t, PAD),
    "long_tool_padded": (lambda t: t + [_dummy(DUMMY_LONG)], PAD),
}
FULL_DEFAULT = ("base", "long_tool", "tool_first", "padded_prompt")


def run(cases, names, runs, workers):
    import julius.brain.cloud as jc
    import julius.brain.tools as jt

    brain, tracker, system = jc.get_brain(), jc.get_tracker(), re_.system_prompt()
    brain.ignore_offline_window = True  # an eval: one slow case fails only itself
    base_tools = list(jt.SCHEMAS)
    results: dict[str, dict[str, list[bool]]] = {c["id"]: {n: [] for n in names} for c in cases}
    usd = 0.0
    try:
        for name in names:  # tool lists are process-wide, so perturbations run one after another and cases in parallel
            tools_fn, extra = PERTURBATIONS[name]
            jt.SCHEMAS[:] = tools_fn(copy.deepcopy(base_tools))
            jobs = [c for c in cases for _ in range(runs)]
            before = sum(tracker.today_by_tier().values())
            with ThreadPoolExecutor(workers) as ex:
                got = list(
                    ex.map(lambda c, e=extra: re_.brain_turn(c, brain, tracker, system + e), jobs)
                )
            usd += sum(tracker.today_by_tier().values()) - before
            for c, r in zip(jobs, got, strict=True):
                results[c["id"]][name].append(all(re_.judge(c, r)))
            print(f"  perturbation {name}: done", flush=True)
    finally:
        jt.SCHEMAS[:] = base_tools
    return results, usd


def report(cases, names, results, threshold):
    by_id = {c["id"]: c for c in cases}
    rows, fragile = [], []
    width = max(len(n) for n in names)
    print()
    print(
        f"{'case':6} {'rate':>5}  " + "  ".join(f"{n:>{max(width, 8)}}" for n in names) + "  text"
    )
    for cid, per in results.items():
        flat = [ok for n in names for ok in per[n]]
        rate = sum(flat) / len(flat)
        cells = [f"{sum(per[n])}/{len(per[n])}" for n in names]
        flag = "" if rate >= threshold else "  <-- FRAGILE"
        print(
            f"{cid:6} {rate:5.0%}  "
            + "  ".join(f"{c:>{max(width, 8)}}" for c in cells)
            + f"  {by_id[cid]['text'][:46]!r}{flag}"
        )
        if rate < threshold:
            fragile.append(cid)
        rows.append(
            {
                "id": cid,
                "category": by_id[cid].get("cat", ""),
                "pass_rate": round(rate, 3),
                **{f"pass_{n}": f"{sum(per[n])}/{len(per[n])}" for n in names},
                "fragile": rate < threshold,
            }
        )
    return rows, fragile


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=("fragile", "full"), default="fragile")
    ap.add_argument("--only", help="comma-separated case ids (overrides --mode's case choice)")
    ap.add_argument("--runs", type=int, help="runs per case and perturbation (default 3, full 1)")
    ap.add_argument("--perturbations", help=f"comma-separated, from: {', '.join(PERTURBATIONS)}")
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()

    names = (
        a.perturbations.split(",")
        if a.perturbations
        else list(PERTURBATIONS)
        if a.mode == "fragile"
        else list(FULL_DEFAULT)
    )
    bad = [n for n in names if n not in PERTURBATIONS]
    if bad:
        raise SystemExit(f"unknown perturbation: {bad}; choose from {list(PERTURBATIONS)}")
    runs = a.runs or (3 if a.mode == "fragile" else 1)

    cases = re_.load_cases(set(a.only.split(",")) if a.only else None)
    _rows, pending = re_.run_shortcuts(cases)  # only cases the brain decides are shaken
    if not a.only and a.mode == "fragile":
        pending = [c for c in pending if c.get("fragile")]
    if not pending:
        raise SystemExit("no brain cases selected")
    print(
        f"shake: {len(pending)} brain cases x {len(names)} perturbations x {runs} runs ({a.mode} mode)"
    )
    results, usd = run(pending, names, runs, a.workers)
    rows, fragile = report(pending, names, results, a.threshold)
    summary = {
        "mode": a.mode,
        "cases": len(rows),
        "perturbations": names,
        "runs": runs,
        "fragile": fragile,
        "usd": round(usd, 4),
        "threshold": a.threshold,
    }
    path, _prev = common.write_results("shake", rows, summary)
    print(
        f"\n{len(rows) - len(fragile)} of {len(rows)} cases robust (>= {a.threshold:.0%} across perturbations); ${usd:.2f}; {path.name}"
    )
    if fragile:
        print(
            "fragile: "
            + ", ".join(fragile)
            + "\nGive each a decisive rule (a TIE-BREAKS line in PLANNING_PROMPT or a 'never when' in the competing tool descriptions), then rerun."
        )
    return 1 if fragile else 0


if __name__ == "__main__":
    sys.exit(main())
