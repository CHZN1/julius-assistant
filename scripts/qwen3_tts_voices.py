import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"

TEXT = (
    "Hey there. Yeah, I'm here. I was actually thinking about that earlier. "
    "It's a pretty good idea. Want to try it and see what happens?"
)

TESTS = {
    "aiden_mature": {
        "speaker": "Aiden",
        "instruct": (
            "Speak as a mature man around 35 years old. "
            "Warm, intelligent, grounded, confident, relaxed, and slightly playful. "
            "Use a lower, fuller speaking register with natural restraint. "
            "Avoid sounding youthful, boyish, overly cheerful, breathy, nasal, "
            "sleepy, or intoxicated. Do not sound like an announcer."
        ),
    },
    "ryan_focused": {
        "speaker": "Ryan",
        "instruct": (
            "Speak as a mature man around 35 years old. "
            "Clear, alert, intelligent, grounded, confident, and conversational. "
            "Keep the delivery crisp, focused, and naturally energetic. "
            "Avoid sounding sleepy, laid-back, sluggish, breathy, stoned, "
            "overly casual, or like an announcer."
        ),
    },
}


OUT_DIR = Path(__file__).resolve().parent / "qwen3_tts_voice_tests_round2"
OUT_DIR.mkdir(exist_ok=True)


def get_device():
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


DEVICE = get_device()

print(f"\nLoading: {MODEL_ID}")
print(f"Device:  {DEVICE}\n")

t0 = time.perf_counter()

model = Qwen3TTSModel.from_pretrained(
    MODEL_ID,
    device_map=DEVICE,
    dtype=torch.float32,
    attn_implementation="sdpa",
)

print(f"Loaded in {time.perf_counter() - t0:.2f}s\n")


for name, cfg in TESTS.items():
    print("=" * 60)
    print(f"TEST:    {name}")
    print(f"SPEAKER: {cfg['speaker']}")
    print("=" * 60)

    start = time.perf_counter()

    wavs, sample_rate = model.generate_custom_voice(
        text=TEXT,
        language="English",
        speaker=cfg["speaker"],
        instruct=cfg["instruct"],
    )

    elapsed = time.perf_counter() - start

    wav = np.asarray(wavs[0], dtype=np.float32)

    output_path = OUT_DIR / f"{name}.wav"
    sf.write(output_path, wav, sample_rate)

    duration = len(wav) / sample_rate
    realtime_factor = elapsed / duration if duration > 0 else 0

    print(f"Saved:       {output_path}")
    print(f"Audio:       {duration:.2f}s")
    print(f"Generation:  {elapsed:.2f}s")
    print(f"RT factor:   {realtime_factor:.2f}x")
    print()


print("=" * 60)
print("DONE")
print("=" * 60)
print(f"\nListen to:\n{OUT_DIR}\n")
