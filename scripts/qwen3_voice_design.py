"""Design a Qwen3 voice from a text description and render samples."""

import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"

TEXT = (
    "Hey there. Yeah, I'm here. I was actually thinking about that earlier. "
    "It's a pretty good idea. Want to try it and see what happens?"
)

# Different interpretations of the same Julius target.
VOICE_DESIGNS = {
    "julius_02_deeper_v2": (
        "American man in his mid-30s with a mature baritone voice. "
        "Lower register, rich and smooth resonance, clear diction, and controlled energy. "
        "Sound warm, intelligent, friendly, curious, and quietly cheerful. "
        "There should be a natural sense of interest and enjoyment in the conversation, "
        "as if he genuinely likes talking to the person and finds things fascinating. "
        "Keep a soft smile in the voice and a lightly playful, amused undertone, "
        "without becoming bubbly, goofy, childish, or overly enthusiastic. "
        "He should sound relaxed and self-assured, but never bored, depressed, detached, "
        "cold, sleepy, sluggish, or uninterested. "
        "Friendly and conversational, but unmistakably adult. "
        "Avoid a youthful tenor, teenage voice, exaggerated bass, raspiness, "
        "stoner-like delivery, vocal fry, or announcer-style performance."
    ),
    "julius_02_deeper_v3": (
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
    ),
}


OUT_DIR = Path(__file__).resolve().parent / "qwen3_voice_design_tests"
OUT_DIR.mkdir(exist_ok=True)


def get_device():
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


DEVICE = get_device()

print()
print(f"Model:  {MODEL_ID}")
print(f"Device: {DEVICE}")
print("First run may download the 1.7B VoiceDesign model.")
print()

load_start = time.perf_counter()

model = Qwen3TTSModel.from_pretrained(
    MODEL_ID,
    device_map=DEVICE,
    dtype=torch.float32,
    attn_implementation="sdpa",
)

print(f"Loaded in {time.perf_counter() - load_start:.2f}s")
print()


for name, instruct in VOICE_DESIGNS.items():
    print("=" * 70)
    print(name)
    print("=" * 70)

    start = time.perf_counter()

    wavs, sample_rate = model.generate_voice_design(
        text=TEXT,
        language="English",
        instruct=instruct,
        do_sample=True,
        temperature=0.8,
        top_p=0.95,
        top_k=50,
    )

    elapsed = time.perf_counter() - start

    wav = np.asarray(wavs[0], dtype=np.float32)

    output_path = OUT_DIR / f"{name}.wav"
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
print(f"\nListen to the four files in:\n{OUT_DIR}\n")
