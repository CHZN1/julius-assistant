"""Voice-selection experiment (generation script, not a unit test). Run with .venv-qwen-tts/bin/python scripts/voice_v3_finalists.py"""
from pathlib import Path
import random
import time

import numpy as np
import soundfile as sf
import torch

from qwen_tts import Qwen3TTSModel


MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"

VOICE_INSTRUCT = (
    "Mature American male around 35 years old with a smooth, medium-low baritone voice. "
    "Warm, intelligent, confident, approachable, and naturally upbeat. "
    "Speak with calm authority but also clear curiosity and genuine friendliness. "
    "The voice should carry a subtle smile and an easy sense of amusement, "
    "like someone who is wise and powerful but still genuinely enjoys discovering new things. "
    "Use lively but controlled conversational rhythm, with enough energy to sound engaged "
    "without sounding hyper, jolly, theatrical, or childish. "
    "He should feel welcoming and personable, not stern, brooding, bored, or emotionally flat. "
    "Avoid teenage brightness, gravel, vocal fry, sleepy pacing, stoner cadence, "
    "exaggerated bass, or radio-announcer delivery."
)

# Same text for EVERY candidate.
# This gives us calm speech, curiosity, amusement, confidence, and a question.
TEXT = (
    "Hey there. Yeah, I'm here. Oh, that's actually pretty interesting. "
    "I like where you're going with this. There's definitely something there. "
    "Alright, walk me through it — what are you thinking?"
)

SEEDS = [
    101,
    203,
    307,
    409,
    503,
    607,
    709,
    811,
]

OUT_DIR = Path(__file__).resolve().parent.parent / "julius_v3_finalists"
OUT_DIR.mkdir(exist_ok=True)

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


print()
print(f"Model:  {MODEL_ID}")
print(f"Device: {DEVICE}")
print()

load_start = time.perf_counter()

model = Qwen3TTSModel.from_pretrained(
    MODEL_ID,
    device_map=DEVICE,
    dtype=torch.float32,
    attn_implementation="sdpa",
)

print(f"Loaded in {time.perf_counter() - load_start:.2f}s\n")


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

    output = OUT_DIR / f"julius_finalist_{i:02d}_seed_{seed}.wav"
    sf.write(output, wav, sample_rate)

    duration = len(wav) / sample_rate

    print(f"Saved:      {output.name}")
    print(f"Audio:      {duration:.2f}s")
    print(f"Generation: {elapsed:.2f}s")
    print()


print("=" * 70)
print("DONE")
print("=" * 70)
print(f"\nListen to:\n{OUT_DIR}\n")