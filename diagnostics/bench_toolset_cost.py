"""A/B cost of the Haiku tool list: trimmed per turn (the previous behaviour) vs the full list, on the same 8-turn mixed session.

The nonce only isolates the system prompt: tools come first in the cached prefix, so the tool-list caches may already be
warm from earlier runs. Compare the steady-state (cache-read) rows and token counts, not the first row. Real API calls (about $0.003).
"""

import ast
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["JULIUS_BACKEND"] = "cloud"
sys.path.insert(0, str(ROOT))
import julius_cloud as jc
import julius_costs as costs
import julius_tools as jt


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


SESSION = [
    "What's the weather like in Springfield right now?",
    "What's Bitcoin trading at?",
    "And what about Ethereum?",
    "What time is it in Tokyo?",
    "Who won the Giants game yesterday?",
    "What about the Dodgers?",
    "What's 17 times 4839?",
    "And what's the weather in Shelbyville?",
]
brain = jc.get_brain()
tracker = jc.get_tracker()
client = brain.client()


def run(variant):
    system = candidate_system() + f"\n\n[benchmark session {uuid.uuid4().hex}]"
    total = 0.0
    rows = []
    for text in SESSION:
        kw = brain.request(
            [{"role": "system", "content": system}, {"role": "user", "content": text}], jt.SCHEMAS
        )  # full list + escalate
        if variant == "trimmed":  # the previous behaviour: this turn's selected tools + escalate
            kw["tools"] = jc.convert_tools(jt.select_schemas(text) + [jc.ESCALATE_SCHEMA])
        kw["max_tokens"] = 200
        r = client.messages.create(**kw)
        usd = costs.cost_of("haiku", r.usage)
        tracker.record("haiku", r.usage, note=f"toolset_ab_{variant}")
        total += usd
        u = r.usage
        rows.append(
            (
                len(kw["tools"]),
                u.input_tokens,
                u.cache_creation_input_tokens,
                u.cache_read_input_tokens,
                u.output_tokens,
                usd,
            )
        )
    return total, rows


for variant in ("trimmed", "full"):
    total, rows = run(variant)
    print(
        f"== {variant}: {len(SESSION)} turns, total ${total:.5f}, mean ${total / len(SESSION):.5f} per turn"
    )
    for text, (n, i, w, rd, o, usd) in zip(SESSION, rows):
        print(
            f"   tools={n:2d} fresh_in={i:4d} cache_write={w:5d} cache_read={rd:5d} out={o:3d} ${usd:.5f}  {text}"
        )
