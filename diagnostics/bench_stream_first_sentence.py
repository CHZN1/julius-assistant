"""Time to the first speakable sentence for a plain chat question: non-streamed (before) vs streamed with tools (after)."""

import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
    if line.startswith("ANTHROPIC_API_KEY="):
        os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip("'\"")
import julius_cloud
import julius_tools as jt
from julius_speech_text import BREVITY_PROMPT, SPOKEN_STYLE_PROMPT, TOOL_POLICY_PROMPT

brain = julius_cloud.get_brain(offline=lambda *a: None)
SYSTEM = (
    "You are Julius, a voice assistant." + SPOKEN_STYLE_PROMPT + TOOL_POLICY_PROMPT + BREVITY_PROMPT
)
MSGS = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": "explain overfitting simply"},
]
SENT = re.compile(r"[.!?…](\s|$)")


def before():
    t = time.perf_counter()
    r = brain.chat(MSGS, tools=jt.SCHEMAS)
    return time.perf_counter() - t  # speech starts when the whole reply is in


def after():
    t = time.perf_counter()
    buf = ""
    for p in brain.chat(MSGS, tools=jt.SCHEMAS, stream=True):
        buf += p.message.content or ""
        if SENT.search(buf):
            return time.perf_counter() - t


before()
after()  # warm the prompt cache and connection
b = [before() for _ in range(5)]
a = [after() for _ in range(5)]
print(f"before (non-streamed): median {statistics.median(b):.2f}s  runs {[round(x, 2) for x in b]}")
print(f"after  (streamed):     median {statistics.median(a):.2f}s  runs {[round(x, 2) for x in a]}")
