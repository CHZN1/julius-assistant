"""Distant-speaker simulation: the 60 wake3+wake4 recordings attenuated to mimic standing further from the microphone (room noise kept at its real level), run through the production
cascade (stage 1 -> Whisper) with the stream's speech-level floor at the old 0.0045 and at lower values. Reports the miss rate per gain.
Usage: python evals/wake_farfield.py [gain ...]"""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wake"))
from julius.audio.listening import has_voice_activity
from julius.wake.stream import WakeStream, run_clip, whisper_verify


def main(gains, floors=(0.0045, 0.0015)):
    base = whisper_verify()

    def verify(clip):
        if not has_voice_activity(clip, 16000, 0.0015):
            return False, "", 0.0  # the production pre-check for wake clips
        return base(clip)

    clips = [
        sf.read(f, dtype="float32")[0]
        for s in ("wake3", "wake4")
        for f in sorted((ROOT / "diagnostics" / "wake_real" / s).glob(f"{s}_*.wav"))
    ]
    rng = np.random.default_rng(3)
    out = {}
    for g in gains:
        for fl in floors:
            miss = 0
            for x in clips:
                y = (x * g + rng.standard_normal(len(x)) * 0.0003).astype(np.float32)
                ev = run_clip(
                    WakeStream(
                        str(ROOT / "julius" / "wake" / "models" / "julius.onnx"),
                        0.05,
                        verify=verify,
                        level_floor=fl,
                    ),
                    y,
                )
                miss += not any(e["accepted"] for e in ev)
            out[f"gain {g} floor {fl}"] = f"{miss}/{len(clips)} missed"
            print(
                f"gain {g}  level floor {fl}: {miss}/{len(clips)} missed ({100 * miss / len(clips):.1f}%)",
                flush=True,
            )
    return out


if __name__ == "__main__":
    fl = [float(a[2:]) for a in sys.argv[1:] if a.startswith("f=")] or [0.0045, 0.0015]
    main([float(a) for a in sys.argv[1:] if not a.startswith("f=")] or [1.0, 0.4, 0.2], tuple(fl))
