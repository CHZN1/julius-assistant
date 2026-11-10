"""Token budget of what Haiku receives every turn: system prompt + tool schemas (counted by the API, no generation).

Usage: python evals/prompt_budget.py [project_dir]   (default: this project; point it at an older checkout to compare)"""

import ast
import json
import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
os.chdir(root)
os.environ["JULIUS_BACKEND"] = "cloud"
import julius.brain.cloud as jc
import julius.brain.tools as jt
from julius.brain.vision_mode import vision_capability_prompt

ns = {}
for n in ast.parse((root / "julius/runtime.py").read_text()).body:
    if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Name) and t.id == "SYSTEM" for t in n.targets
    ):
        exec(compile(ast.Module(body=[n], type_ignores=[]), "<c>", "exec"), ns)
system = ns["SYSTEM"] + vision_capability_prompt(False) + jc.PLANNING_PROMPT
tools = jc.convert_tools(list(jt.SCHEMAS) + [jc.ESCALATE_SCHEMA])
client = jc.make_client()


def count(**kw):
    return client.messages.count_tokens(
        model=jc.get_brain().model, messages=[{"role": "user", "content": "hi"}], **kw
    ).input_tokens


base = count()
out = {
    "system_tokens": count(system=system) - base,
    "tools_tokens": count(tools=tools) - base,
    "tool_count": len(tools),
}
out["total_prefix_tokens"] = out["system_tokens"] + out["tools_tokens"]
print(json.dumps(out))
