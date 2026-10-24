import json
import re
import time

from mlx_lm import load, generate
import julius_tools as jt


MODEL_ID = "mlx-community/Qwen3-8B-4bit"

SYSTEM = """You are Julius, the user's local personal assistant.

Use a tool when the request requires one of the available tools.
For calculations, ALWAYS use calculate rather than doing the arithmetic yourself.
For the current local date/time, ALWAYS use get_datetime.
Do not call a tool for normal conversation or explanations.

Keep normal answers concise, warm, intelligent, and conversational.
Do not use emoji.
Do not end with generic offers such as "Need anything else?"
"""

TESTS = [
    {"name": "calculator tool", "user": "What is 17 times 4839?", "expect_tool": "calculate"},
    {"name": "datetime tool", "user": "What time is it right now?", "expect_tool": "get_datetime"},
    {"name": "no tool", "user": "Explain overfitting in one sentence.", "expect_tool": None},
]

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)


def render(messages):
    return tokenizer.apply_chat_template(
        messages,
        tools=jt.SCHEMAS,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )


def run_generation(messages, max_tokens=256):
    prompt = render(messages)
    t0 = time.perf_counter()
    text = generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=max_tokens,
        verbose=False,
    )
    return text.strip(), time.perf_counter() - t0


def parse_tool_calls(text):
    calls = []
    for raw in TOOL_CALL_RE.findall(text):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Malformed tool JSON:\n{raw}\n\n{exc}") from exc

        name = obj.get("name")
        args = obj.get("arguments", {})

        if not isinstance(name, str) or not name:
            raise RuntimeError(f"Tool call missing a valid name: {obj}")

        if isinstance(args, str):
            args = json.loads(args)

        if not isinstance(args, dict):
            raise RuntimeError(f"Tool arguments must be an object: {obj}")

        calls.append({"name": name, "arguments": args})
    return calls


print("=" * 72)
print("QWEN3-8B MLX + JULIUS TOOL-CALL TEST")
print("=" * 72)
print(f"Model: {MODEL_ID}")
print(f"Julius schemas: {len(jt.SCHEMAS)}")
print()

t0 = time.perf_counter()
model, tokenizer = load(MODEL_ID)
print(f"Loaded in {time.perf_counter() - t0:.2f}s\n")

passed = 0

for i, test in enumerate(TESTS, start=1):
    print("=" * 72)
    print(f"TEST {i}: {test['name']}")
    print(f"YOU: {test['user']}")

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": test["user"]},
    ]

    first, first_s = run_generation(messages)
    print(f"MODEL PHASE 1: {first}")
    print(f"Phase 1: {first_s:.2f}s")

    calls = parse_tool_calls(first)
    expected = test["expect_tool"]

    if expected is None:
        if calls:
            print(f"FAIL: expected no tool, got {[c['name'] for c in calls]}\n")
            continue
        print("PASS: answered without a tool")
        print(f"FINAL: {first}\n")
        passed += 1
        continue

    if not calls:
        print(f"FAIL: expected tool '{expected}', but no tool call was emitted.\n")
        continue

    if calls[0]["name"] != expected:
        print(f"FAIL: expected '{expected}', got {[c['name'] for c in calls]}\n")
        continue

    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": c["name"],
                        "arguments": c["arguments"],
                    },
                }
                for c in calls
            ],
        }
    )

    for call in calls:
        name = call["name"]
        args = call["arguments"]

        print(f"TOOL CALL: {name}({args})")
        result = jt.call(name, args)
        print(f"TOOL RESULT: {result}")

        messages.append({"role": "tool", "name": name, "content": result})

    final, final_s = run_generation(messages)
    print(f"FINAL: {final}")
    print(f"Phase 2: {final_s:.2f}s")

    second_calls = parse_tool_calls(final)
    if second_calls:
        print(f"FAIL: model emitted another tool call: {[c['name'] for c in second_calls]}\n")
        continue

    print(f"PASS: {expected} tool round-trip completed\n")
    passed += 1

print("=" * 72)
print(f"RESULT: {passed}/{len(TESTS)} tests passed")
print("=" * 72)

if passed != len(TESTS):
    raise SystemExit(1)

print("PASS: MLX Qwen3-8B works with Julius's real tool schemas.")
