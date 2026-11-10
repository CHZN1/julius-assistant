import time

from mlx_lm import load, generate


MODEL_ID = "mlx-community/Qwen3-8B-4bit"

SYSTEM = (
    "You are Julius, the user's local personal assistant. "
    "Be warm, intelligent, concise, conversational, and slightly playful."
)

TESTS = [
    "Hey Julius, explain what overfitting is in one sentence.",
    "What is 17 times 4839?",
    "I think I found a way to improve your memory system. What do you think?",
]


print("=" * 70)
print("QWEN3-8B 4-BIT MLX TEST")
print("=" * 70)
print(f"Model: {MODEL_ID}\n")

t0 = time.perf_counter()

model, tokenizer = load(MODEL_ID)

print(f"Loaded in {time.perf_counter() - t0:.2f}s\n")


for i, user_text in enumerate(TESTS, start=1):

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_text},
    ]

    prompt = tokenizer.apply_chat_template(
    messages,
    tokenize=False,
    add_generation_prompt=True,
    enable_thinking=False,
    )   

    print("=" * 70)
    print(f"TEST {i}")
    print(f"YOU: {user_text}")

    t0 = time.perf_counter()

    response = generate(
        model,
        tokenizer,
        prompt=prompt,
        max_tokens=200,
        verbose=False,
    )

    elapsed = time.perf_counter() - t0

    print(f"JULIUS: {response.strip()}")
    print(f"Generation: {elapsed:.2f}s\n")


print("=" * 70)
print("PASS: MLX Qwen3-8B generation completed")
print("=" * 70)