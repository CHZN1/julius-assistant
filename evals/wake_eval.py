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
from wake.stream import run_clip

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
    def talk(sessions):
        secs = fires = 0
        for sess in sessions:
            for f, a in load(sess):
                secs += len(a) / SR + 1.5; fires += oww_scores(model, a)[name] >= thr
        return secs, fires
    jv = sorted((ROOT / "wake" / "data" / "eval_julius").glob("*.wav"))
    if jv:
        fired = [p.name for p in jv if oww_scores(model, sf.read(p, dtype="float32")[0])[name] >= thr]
        print(f"Julius's own voice (held-out lines, {len(jv)} clips, real Qwen clone): {len(fired)} wakes" + (f" {fired}" if fired else ""))
    for label, sessions in (("talk3 (held out, never trained on)", ["talk3"]), ("talk1+talk2 (used as training negatives)", ["talk1", "talk2"])):
        secs, fires = talk(sessions)
        if secs: print(f"false wakes, {label}: {fires} in {secs / 60:.1f} min = {fires / (secs / 3600):.1f} per hour")

def stream_stats(stream, sessions_clips, gap=1.5):
    """Run the stream over clips (each followed by `gap` s of silence); return accepted events, stage-1 fires, decision delays, Whisper seconds and audio seconds."""
    acc = fires = 0; delays = []; wsecs = 0.0; audio = 0.0; detail = []
    for name, a in sessions_clips:
        ev = run_clip(stream, a); audio += len(a) / SR + gap
        fires += stream.fires; acc += sum(e["accepted"] for e in ev); wsecs += sum(e["verify_s"] for e in ev)
        delays += [e["decided"] - e["fire"] for e in ev if e["accepted"]]
        detail.append((name, [e for e in ev]))
    return {"accepted": acc, "fires": fires, "audio_s": audio, "whisper_s": wsecs, "delays": delays, "detail": detail}

def thresholds():
    """python evals/wake_eval.py thresholds MODEL : sweep on TRAIN-SIDE data only (wake and talk1+talk2) to choose thresholds; wake2 and talk3 are not touched."""
    from openwakeword.model import Model
    m = Model(wakeword_models=[sys.argv[2]], inference_framework="onnx"); n = list(m.models)[0]
    wk = np.array([oww_scores(m, a)[n] for _, a in load("wake")]); tk = [oww_scores(m, a)[n] for s in ("talk1", "talk2") for _, a in load(s)]
    tk = np.array(tk); secs = sum(len(a) / SR + 1.5 for s in ("talk1", "talk2") for _, a in load(s))
    print("wake peaks (train-side): min %.2f median %.2f" % (wk.min(), np.median(wk)))
    for t in (.05, .1, .2, .3, .5, .7, .9): print(f"thr {t}: miss {100 * (wk < t).mean():.1f}%  stage-1 fires on talk1+2 {int((tk >= t).sum())} ({(tk >= t).sum() / (secs / 3600):.0f}/h)")

def compare():
    """python evals/wake_eval.py compare MODEL DETECTOR_THR CASCADE_THR  : retrained detector alone vs the two-stage cascade, same metrics."""
    from wake.stream import WakeStream, run_clip, whisper_verify
    import julius_listening
    model, t_det, t_cas = sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
    verify = whisper_verify()
    engines = {"detector alone": WakeStream(model, t_det), "cascade (low threshold + Whisper)": WakeStream(model, t_cas, verify=verify)}
    wake = load("wake2"); talk3 = load("talk3"); jv = [(p.name, sf.read(p, dtype="float32")[0]) for p in sorted((ROOT / "wake" / "data" / "eval_julius").glob("*.wav"))]
    if not wake: sys.exit("no wake2 recordings: python diagnostics/record_wake.py wake2")
    # noise-floor idle cost: 60 s of room-level noise through stage 1
    rng = np.random.default_rng(0); idle = (rng.standard_normal(60 * SR) * 0.003).astype(np.float32)
    res = {}
    for label, st in engines.items():
        w = stream_stats(st, wake); t = stream_stats(st, talk3); j = stream_stats(st, jv)
        st.reset(); xi = (idle * 32767).astype(np.int16); c0 = time.process_time()
        for i in range(0, len(xi) - FRAME + 1, FRAME): st.push(xi[i:i + FRAME])
        cpu = 100 * (time.process_time() - c0) / 60
        hours = t["audio_s"] / 3600
        res[label] = {"miss_pct": round(100 * (1 - w["accepted"] / len(wake)), 1), "missed": [n for n, ev in w["detail"] if not any(e["accepted"] for e in ev)],
                      "false_wakes_per_hour": round(t["accepted"] / hours, 1), "stage1_fires_per_hour_talk": round(t["fires"] / hours, 1),
                      "julius_voice_wakes": j["accepted"], "julius_voice_stage1_fires": j["fires"], "idle_cpu_pct_of_core": round(cpu, 2),
                      "whisper_runs_per_hour": round(t["fires"] / hours, 1) if label.startswith("cascade") else 0,
                      "whisper_s_per_hour": round(t["whisper_s"] / hours, 1) if label.startswith("cascade") else 0,
                      "decision_delay_after_fire_s": round(float(np.median(w["delays"])), 2) if w["delays"] else None, "talk3_min": round(t["audio_s"] / 60, 1)}
        print(label, json.dumps(res[label], indent=1))
    (REAL / "compare.json").write_text(json.dumps(res, indent=1))

if __name__ == "__main__":
    {"baseline": baseline, "oww": lambda: oww(sys.argv[2]), "compare": compare, "thresholds": thresholds}[sys.argv[1] if len(sys.argv) > 1 else "baseline"]()
