"""Build the synthetic wake-word training audio under wake/data/ (git-ignored), 16 kHz mono int16.

  pos/          "Julius" / "Hey Julius" / "Julius, <request>" in Kokoro voices other than Julius's own, with speed, pitch, noise and room variation
  neg_hard/     sound-alikes (Julia, Julian, jealous, July ...) in the same voices and augmentations
  neg_general/  ordinary sentences and other assistants' wake phrases, same voices
  neg_julius/   Julius's own voices (the Qwen clone and Kokoro am_adam, his legacy voice) saying his name and his usual replies; these must never wake him
Usage: python wake/build_dataset.py [--per-class N] [--julius-voice]
Positives never use Julius's voice. The recorded sessions in diagnostics/wake_real/ are eval-only and never enter training."""

import argparse
import json
import random
import subprocess
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

from julius.paths import REPO_ROOT
from julius.wake import phrases as P

ROOT = REPO_ROOT

DATA = ROOT / "wake" / "data"
SR = 16000
JULIUS_VOICES = {
    "am_adam"
}  # legacy Kokoro voice of Julius; the live voice is the seed-1301 Qwen clone (julius_mlx_tts_worker.py)


def resample(x, sr_in):
    return (
        x
        if sr_in == SR
        else signal.resample_poly(x, SR // np.gcd(SR, sr_in), sr_in // np.gcd(SR, sr_in)).astype(
            np.float32
        )
    )


def pitch(x, semitones):
    import librosa

    return librosa.effects.pitch_shift(x, sr=SR, n_steps=semitones) if abs(semitones) > 0.05 else x


def noise_like(kind, n, rng):
    w = rng.standard_normal(n).astype(np.float32)
    if kind == "white":
        return w
    if kind == "pink":
        f = np.fft.rfft(w)
        f /= np.sqrt(np.maximum(np.arange(len(f)), 1))
        return np.fft.irfft(f, n).astype(np.float32)
    if kind == "hum":
        t = np.arange(n) / SR
        return (np.sin(2 * np.pi * 60 * t) + 0.5 * np.sin(2 * np.pi * 120 * t)).astype(
            np.float32
        ) + 0.1 * w
    b, a = signal.butter(2, 600 / (SR / 2))
    return signal.lfilter(b, a, w).astype(np.float32)  # "room": low rumble


def augment(x, rng):
    x = pitch(x, rng.uniform(-2.5, 2.5))
    if rng.random() < 0.5:  # small room: exponentially decaying noise impulse response
        rt = rng.uniform(0.08, 0.45)
        n = int(rt * SR)
        ir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)
        ir[0] = 1
        x = signal.fftconvolve(x, ir / np.abs(ir).sum() * 2.5)[: len(x) + n // 2].astype(np.float32)
    if rng.random() < 0.3:  # cheap microphone: band limit
        b, a = signal.butter(
            2, [rng.uniform(100, 250) / (SR / 2), rng.uniform(3500, 7000) / (SR / 2)], "band"
        )
        x = signal.lfilter(b, a, x).astype(np.float32)
    x = (
        x / (np.abs(x).max() + 1e-6) * float(np.exp(rng.uniform(np.log(0.03), np.log(0.9))))
    )  # quiet and far takes down to about -30 dBFS
    lead, tail = rng.uniform(0.2, 1.0), rng.uniform(0.3, 1.0)
    y = np.concatenate(
        [np.zeros(int(lead * SR), np.float32), x, np.zeros(int(tail * SR), np.float32)]
    )
    if rng.random() < 0.85:
        snr = rng.uniform(4, 30)
        nz = noise_like(rng.choice(["white", "pink", "hum", "room"]), len(y), rng)
        y = y + nz / (np.sqrt((nz**2).mean()) + 1e-9) * np.sqrt((x**2).mean()) / (10 ** (snr / 20))
    return np.clip(y, -1, 1)


def save(path, y):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, (np.clip(y, -1, 1) * 32767).astype(np.int16), SR, subtype="PCM_16")


def kokoro_clips(pipe, text, voice, speed):
    parts = [
        np.asarray(a.detach().cpu() if hasattr(a, "detach") else a, dtype=np.float32)
        for _, _, a in pipe(text, voice=voice, speed=speed)
    ]
    return resample(np.concatenate(parts), 24000) if parts else None


def julius_voice_clips(lines):
    """Julius's live voice: the isolated MLX Qwen worker with his reference prompts (read-only use; the voice itself is not changed)."""
    exe = ROOT / ".venv-mlx-tts" / "bin" / "python"
    proc = subprocess.Popen(
        [str(exe), "-m", "julius.audio.mlx_tts_worker"],
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert json.loads(proc.stdout.readline()).get("ready")
    out = []
    for line in lines:
        proc.stdin.write(json.dumps({"text": line}) + "\n")
        proc.stdin.flush()
        chunks = []
        sr = 24000
        while True:
            msg = json.loads(proc.stdout.readline())
            if msg.get("chunk"):
                chunks.append(np.load(msg["path"]))
                sr = msg["sample_rate"]
            elif msg.get("done") or msg.get("error"):
                break
        if chunks:
            out.append((line, resample(np.concatenate(chunks).astype(np.float32), sr)))
    proc.stdin.write(json.dumps({"op": "close"}) + "\n")
    proc.stdin.flush()
    proc.wait(timeout=30)
    return out


def user_positives(rng, per_clip=24):
    """Your recorded wake utterances (sessions wake, wake2 and quick) with many augmentations; never the final eval sets wake3, wake4 or talk3."""
    out = []
    files = [
        p
        for s in ("wake", "wake2", "quick")
        for p in sorted((ROOT / "diagnostics" / "wake_real" / s).glob(f"{s}_*.wav"))
    ]
    for p in files:
        x, _ = sf.read(p, dtype="float32")
        for k in range(per_clip):
            y = x[int(rng.integers(0, int(0.15 * SR))) :] if k else x
            if k % 3 == 1:
                y = signal.resample_poly(y, 100, int(rng.integers(88, 113))).astype(
                    np.float32
                )  # small speed change
            name = f"pos/user_{p.stem}_{k:02d}.wav"
            save(DATA / name, augment(y, rng))
            out.append({"file": name, "text": p.stem, "voice": "user", "speed": 1.0})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=1500)
    ap.add_argument("--julius-voice", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--classes", default="pos,neg_hard,neg_general,julius")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument(
        "--eval-julius",
        action="store_true",
        help="render the held-out Julius-voice eval clips (wake/data/eval_julius)",
    )
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    random.seed(args.seed)
    if args.classes == "user":
        m = user_positives(np.random.default_rng(args.seed))
        (DATA / "manifest_user.json").write_text(json.dumps(m))
        print("done", len(m))
        return
    if args.eval_julius:
        for k, (_text, x) in enumerate(julius_voice_clips(P.JULIUS_OWN_EVAL)):
            save(
                DATA / "eval_julius" / f"qwen_{k:02d}.wav",
                np.concatenate(
                    [
                        np.zeros(int(0.5 * SR), np.float32),
                        x / (np.abs(x).max() + 1e-6) * 0.6,
                        np.zeros(int(0.8 * SR), np.float32),
                    ]
                ),
            )
        print("wrote", len(list((DATA / "eval_julius").glob("*.wav"))), "clips")
        return
    import glob
    import os

    from kokoro import KPipeline

    pipe = KPipeline(lang_code="a")
    hub = glob.glob(
        os.path.expanduser(
            "~/.cache/huggingface/hub/models--hexgrad--Kokoro-82M/snapshots/*/voices"
        )
    )[0]
    voices = sorted(Path(v).stem for v in glob.glob(hub + "/*.pt"))
    other = [v for v in voices if v not in JULIUS_VOICES]
    print(
        "positive/negative voices:",
        other,
        "| Julius's legacy Kokoro voice (hard negative only):",
        sorted(JULIUS_VOICES),
        flush=True,
    )

    def pos_text():
        return (
            random.choice(P.BARE)
            if random.random() < 0.5
            else random.choice(P.PREFIXES) + random.choice(P.REQUESTS)
        )

    classes = {
        "pos": pos_text,
        "neg_hard": lambda: random.choice(
            P.SOUNDALIKES + P.SOUNDALIKE_SENTENCES + P.soundalike_sentences() * 2
        ),
        "neg_general": lambda: random.choice(P.GENERAL),
    }
    manifest = []
    want = args.classes.split(",")
    for cls, make in classes.items():
        if cls not in want:
            continue
        t0 = time.time()
        for i in range(args.start, args.start + args.per_class):
            text, voice, speed = make(), random.choice(other), random.uniform(0.8, 1.3)
            x = kokoro_clips(pipe, text, voice, speed)
            if x is None or len(x) < SR * 0.2:
                continue
            name = f"{cls}/{cls}_{i:05d}.wav"
            save(DATA / name, augment(x, rng))
            manifest.append({"file": name, "text": text, "voice": voice, "speed": round(speed, 2)})
            if i % 200 == 0:
                print(f"{cls} {i}/{args.per_class} {time.time() - t0:.0f}s", flush=True)
    own = 0
    if "julius" not in want:
        (DATA / f"manifest_{args.classes.replace(',', '_')}_{args.start}.json").write_text(
            json.dumps(manifest)
        )
        print("done", len(manifest))
        return
    for text in P.JULIUS_OWN:  # Kokoro am_adam, several speeds
        for speed in (0.9, 1.0, 1.1):
            x = kokoro_clips(pipe, text, "am_adam", speed)
            for _ in range(4):
                name = f"neg_julius/kokoro_{own:04d}.wav"
                save(DATA / name, augment(x, rng))
                manifest.append({"file": name, "text": text, "voice": "am_adam", "speed": speed})
                own += 1
    if args.julius_voice:
        for text, x in julius_voice_clips(P.JULIUS_OWN):
            for _ in range(6):
                name = f"neg_julius/qwen_{own:04d}.wav"
                save(DATA / name, augment(x, rng))
                manifest.append({"file": name, "text": text, "voice": "julius-qwen", "speed": 1.0})
                own += 1
    (DATA / "manifest_julius.json").write_text(json.dumps(manifest, indent=0))
    print("done", len(manifest))


if __name__ == "__main__":
    main()
