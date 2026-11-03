"""Wake-word eval on your recordings (diagnostics/wake_real/, local only). Free to run.

  python evals/wake_eval.py baseline     Whisper-transcript wake path (today) and the pretrained openWakeWord models on the wake session
  python evals/wake_eval.py oww MODEL    one openWakeWord model file/name on the wake session (miss rate) and every talk session (false wakes per hour)
Miss rate = wake recordings where the engine did not fire; false wakes per hour = fires on ordinary talk, with 1.5 s of silence between
sentences, scaled to an hour. Targets: miss rate <= 3%, false wakes <= 1 per hour.
"""
import json, sys, time
from pathlib import Path
import numpy as np, soundfile as sf
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
REAL = ROOT / "diagnostics" / "wake_real"
SR, FRAME = 16000, 1280  # openWakeWord reads 80 ms frames

def load(session):
    files = sorted((REAL / session).glob(f"{session}_*.wav"))
    return [(p.name, sf.read(p, dtype="float32")[0]) for p in files]

def oww_scores(model, audio, tail=0.6):
    """Peak score per model over one clip, feeding 80 ms int16 frames and a little trailing silence so the last frame is flushed."""
    model.reset()
    x = np.concatenate([audio, np.zeros(int(tail * SR), dtype=np.float32)])
    x = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    peak = {}
    for i in range(0, len(x) - FRAME + 1, FRAME):
        for name, s in model.predict(x[i:i + FRAME]).items(): peak[name] = max(peak.get(name, 0.0), float(s))
    return peak

def whisper_wake(clips):
    import mlx_whisper, mlx.core as mx
    import julius_config, julius_listening as jl
    hint = julius_config.asr_initial_prompt()
    repo = "mlx-community/whisper-small.en-mlx"
    def run(a):
        mx.random.seed(1301)
        return mlx_whisper.transcribe(a, path_or_hf_repo=repo, condition_on_previous_text=False, language="en", no_speech_threshold=0.6,
                                      **({"initial_prompt": hint} if hint else {}))["text"].strip()
    run(clips[0][1])
    rows = []
    for name, a in clips:
        t0 = time.perf_counter(); text = run(a); secs = time.perf_counter() - t0
        rows.append({"file": name, "heard": text, "fired": jl.has_wake(text), "secs": round(secs, 3)})
    return rows

def baseline():
    from openwakeword.model import Model
    clips = load("wake")
    if not clips: sys.exit("no recordings: python diagnostics/record_wake.py wake")
    print(f"{len(clips)} wake recordings, {sum(len(a) for _, a in clips) / SR / 60:.1f} min of audio\n")
    rows = whisper_wake(clips)
    miss = [r for r in rows if not r["fired"]]
    print(f"Whisper-transcript path (today): miss rate {100 * len(miss) / len(rows):.1f}% ({len(miss)}/{len(rows)}), "
          f"mean {np.mean([r['secs'] for r in rows]):.2f} s of Whisper per wake utterance")
    for r in miss: print(f"   MISS {r['file']}: heard {r['heard']!r}")
    model = Model(inference_framework="onnx")  # every pretrained model
    peaks = [oww_scores(model, a) for _, a in clips]
    print("\nPretrained openWakeWord models on the same clips (fires at score >= 0.5; none is trained on 'Julius'):")
    for name in peaks[0]: print(f"   {name:14} fired on {sum(p[name] >= .5 for p in peaks):2}/{len(peaks)} clips, max score {max(p[name] for p in peaks):.2f}")
    frame = np.zeros(FRAME, dtype=np.int16); model.predict(frame)
    c0 = time.process_time(); n = 400
    for _ in range(n): model.predict(frame)
    cpu = (time.process_time() - c0) / (n * FRAME / SR)
    print(f"\nCPU, always listening: openWakeWord with all {len(peaks[0])} pretrained models {100 * cpu:.1f}% of one core; "
          f"Whisper path 0% while silent but runs Whisper ({np.mean([r['secs'] for r in rows]):.2f} s) for every loud sound, wake word or not")
    out = {"whisper": rows, "oww_peaks": peaks}
    (REAL / "baseline.json").write_text(json.dumps(out, indent=1))

def oww(model_arg):
    from openwakeword.model import Model
    model = Model(wakeword_models=[model_arg], inference_framework="onnx")
    wake = load("wake"); name = list(model.models)[0]
    thr = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
    peaks = [oww_scores(model, a)[name] for _, a in wake]
    miss = [f for (f, _), p in zip(wake, peaks) if p < thr]
    print(f"miss rate {100 * len(miss) / len(wake):.1f}% ({len(miss)}/{len(wake)}) at threshold {thr}")
    secs = 0; fires = 0
    for s in ("talk1", "talk2", "talk3"):
        for f, a in load(s):
            secs += len(a) / SR + 1.5; fires += oww_scores(model, a)[name] >= thr
    if secs: print(f"false wakes: {fires} in {secs / 60:.1f} min of ordinary talk = {fires / (secs / 3600):.1f} per hour")

if __name__ == "__main__":
    {"baseline": baseline, "oww": lambda: oww(sys.argv[2])}[sys.argv[1] if len(sys.argv) > 1 else "baseline"]()
