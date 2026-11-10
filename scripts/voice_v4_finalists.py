"""Voice-selection experiment (generation script, not a unit test). Run with .venv-qwen-tts/bin/python scripts/voice_v4_finalists.py"""

import random
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"

VOICE_INSTRUCT = (
    "Mature American male around 35 years old with a smooth, medium-low baritone voice. "
    "Warm, intelligent, confident, approachable, and naturally upbeat. "
    "His default tone is calm and composed, but he is emotionally expressive and genuinely curious. "
    "When something interests him, let the voice brighten noticeably with real excitement, wonder, "
    "and amused enthusiasm, while still sounding like the same mature adult man. "
    "He should often sound as though he is smiling slightly when engaged in conversation. "
    "Use a lively, flexible speaking rhythm with natural rises in pitch and energy when fascinated, "
    "then return smoothly to a grounded, confident lower register for serious or thoughtful moments. "
    "He should feel friendly, attentive, playful, and easy to talk to, with quiet authority underneath it. "
    "Do not sound depressed, bored, detached, sleepy, brooding, stern, or emotionally flat. "
    "Avoid teenage brightness, exaggerated cheerfulness, cartoonish excitement, gravel, vocal fry, "
    "stoner cadence, exaggerated bass, or radio-announcer delivery."
)

TEXT = (
    "Hey there. Yeah, I'm here. "
    "Oh — wait, that's actually fascinating! How did you get that working? "
    "Ha, okay, I really like that. "
    "But seriously, there is one thing we should think through before we change anything. "
    "Alright. Show me what you've got."
)

SEEDS = [
    503,
    709,
    907,
    1103,
    1301,
    1601,
    1907,
    2203,
]

OUT_DIR = Path(__file__).resolve().parent.parent / "julius_v4_finalists"
OUT_DIR.mkdir(exist_ok=True)

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


print()
print("=" * 70)
print("JULIUS V4 VOICE FINALISTS")
print("=" * 70)
print(f"Model:  {MODEL_ID}")
print(f"Device: {DEVICE}")
print(f"Output: {OUT_DIR}")
print()

load_start = time.perf_counter()

model = Qwen3TTSModel.from_pretrained(
    MODEL_ID,
    device_map=DEVICE,
    dtype=torch.float32,
    attn_implementation="sdpa",
)

print(f"Model loaded in {time.perf_counter() - load_start:.2f}s\n")


for i, seed in enumerate(SEEDS, start=1):
    set_seed(seed)

    print("=" * 70)
    print(f"FINALIST {i:02d} | seed {seed}")
    print("=" * 70)

    start = time.perf_counter()

    wavs, sample_rate = model.generate_voice_design(
        text=TEXT,
        language="English",
        instruct=VOICE_INSTRUCT,
        do_sample=True,
        temperature=0.8,
        top_p=0.95,
        top_k=50,
    )

    elapsed = time.perf_counter() - start

    wav = np.asarray(wavs[0], dtype=np.float32)

    output_path = OUT_DIR / f"julius_v4_{i:02d}_seed_{seed}.wav"
    sf.write(output_path, wav, sample_rate)

    duration = len(wav) / sample_rate
    rtf = elapsed / duration if duration else 0.0

    print(f"Saved:      {output_path.name}")
    print(f"Audio:      {duration:.2f}s")
    print(f"Generation: {elapsed:.2f}s")
    print(f"RT factor:  {rtf:.2f}x")
    print()


print("=" * 70)
print("DONE")
print("=" * 70)
print()
print("Files:")
for i, seed in enumerate(SEEDS, start=1):
    label = ""
    if seed == 503:
        label = "  <-- previous favorite"
    elif seed == 709:
        label = "  <-- previous runner-up"

    print(f"  {i:02d}. julius_v4_{i:02d}_seed_{seed}.wav{label}")

print(f"\nListen to them in:\n{OUT_DIR}\n")
