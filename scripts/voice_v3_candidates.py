"""Voice-selection experiment (generation script, not a unit test). Run with .venv-qwen-tts/bin/python scripts/voice_v3_candidates.py"""

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
    "Speak with calm authority but also clear curiosity and genuine friendliness. "
    "The voice should carry a subtle smile and an easy sense of amusement, "
    "like someone who is wise and powerful but still genuinely enjoys discovering new things. "
    "Use lively but controlled conversational rhythm, with enough energy to sound engaged "
    "without sounding hyper, jolly, theatrical, or childish. "
    "He should feel welcoming and personable, not stern, brooding, bored, or emotionally flat. "
    "Avoid teenage brightness, gravel, vocal fry, sleepy pacing, stoner cadence, "
    "exaggerated bass, or radio-announcer delivery."
)

LINES = [
    "Hey there. Yeah, I'm here. What's up?",
    "Oh, that's actually pretty interesting. I hadn't thought about it that way.",
    "Yeah, I like that idea. There's definitely something there.",
    "Huh. Okay, now you've got my attention.",
    "That's a good question. Give me a second to think about it.",
    "Yeah, absolutely. That makes a lot more sense.",
    "Oh, that's cool. I want to see where this goes.",
    "Alright, now we're getting somewhere.",
]

OUT_DIR = Path(__file__).resolve().parent.parent / "julius_v3_candidates"
OUT_DIR.mkdir(exist_ok=True)

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"

print(f"Loading {MODEL_ID}")
print(f"Device: {DEVICE}")

t0 = time.perf_counter()

model = Qwen3TTSModel.from_pretrained(
    MODEL_ID,
    device_map=DEVICE,
    dtype=torch.float32,
    attn_implementation="sdpa",
)

print(f"Loaded in {time.perf_counter() - t0:.2f}s\n")

for i, text in enumerate(LINES, start=1):
    print(f"Generating candidate {i}...")

    start = time.perf_counter()

    wavs, sample_rate = model.generate_voice_design(
        text=text,
        language="English",
        instruct=VOICE_INSTRUCT,
        do_sample=True,
        temperature=0.8,
        top_p=0.95,
        top_k=50,
    )

    wav = np.asarray(wavs[0], dtype=np.float32)

    out = OUT_DIR / f"julius_v3_candidate_{i:02d}.wav"
    sf.write(out, wav, sample_rate)

    elapsed = time.perf_counter() - start
    duration = len(wav) / sample_rate

    print(f"  {out.name}\n  audio: {duration:.2f}s\n  generation: {elapsed:.2f}s\n")

print(f"\nDone. Listen to:\n{OUT_DIR}")
