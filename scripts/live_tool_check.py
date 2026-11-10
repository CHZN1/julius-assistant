"""Run real live tools (weather, time, prices, calculator) through the turn function, no devices.

Usage: python -m scripts.live_tool_check
"""

import json
import os
from pathlib import Path

from tests.harness import TurnHarness

BENCHMARK_DIR = Path(os.environ.get("JULIUS_BENCH_DIR", "benchmarks"))
PROMPTS = [
    "What's the weather today?",
    "Is that for Springfield?",
    "What about tomorrow?",
    "What time is it in Tokyo?",
    "How many hours ahead is that?",
    "What about Egypt?",
    "What's the weather in New York?",
    "What's the forecast for this week?",
    "What's 17 times 4839?",
    "What's Bitcoin trading at?",
    "What time is it?",
]
FAILURE_MARKERS = ["couldn't", "failed:", "no data", "no price"]
EXPECTED = {"Tokyo": "Asia/Tokyo", "Egypt": "Africa/Cairo", "4839": "82,263"}


def main() -> None:
    """Ask each prompt in one conversation and check the answers carry the right data."""
    harness = TurnHarness()
    rows = []
    for text in PROMPTS:
        spoken: list[str] = []
        out = harness.ask(text, "", speak_fn=spoken.append)
        tool = harness.session.intent_router.last
        rows.append({"prompt": text, "answer": out, "spoken": spoken, "tool": tool})
        assert spoken == [out]
        assert not any(marker in out.lower() for marker in FAILURE_MARKERS), (text, out)
        for keyword, expected in EXPECTED.items():
            if keyword in text:
                assert expected in out
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    (BENCHMARK_DIR / "candidate_live_tools.json").write_text(json.dumps(rows, indent=2))
    print(f"Turn function completed all {len(PROMPTS)} live tool/follow-up checks")


if __name__ == "__main__":
    main()
