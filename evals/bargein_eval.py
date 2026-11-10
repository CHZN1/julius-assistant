"""Barge-in options, evaluated in isolation on real echo recordings (diagnostics/record_echo.py, local only, wake/data/echo/).

The echo recordings are Julius's own voice played through your speakers and heard by your microphone while you stayed silent. Interruptions
are built by adding your dry recordings (wake / quick / stop sessions, talk name sentences) at known times to the real echo: exact ground truth,
but no double-talk distortion or microphone AGC (stated in the report).

Options: raw energy; (c) level check against the expected echo level; (a) acoustic echo cancellation with the playback as the reference, WebRTC
(aec-audio-processing) and Speex (pyaec); and (a)+(c). Option (b), self-transcript rejection, is scored separately (bargein_transcripts).
Usage: python evals/bargein_eval.py aec     ERLE and echo-only false triggers of each canceller
       python evals/bargein_eval.py run     the full scenario table"""
import json, sys, time
from pathlib import Path
import numpy as np, soundfile as sf
from scipy import signal
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "wake"))
ECHO = ROOT / "wake" / "data" / "echo"; REAL = ROOT / "diagnostics" / "wake_real"
SR, HOP = 16000, 320  # 20 ms frames

def load(name):
    mic, _ = sf.read(ECHO / f"{name}_mic.wav", dtype="float32"); ref, _ = sf.read(ECHO / f"{name}_ref.wav", dtype="float32")
    n = min(len(mic), len(ref)); return mic[:n], ref[:n]

def names(prefix=None):
    out = sorted(p.name[:-8] for p in ECHO.glob("*_mic.wav"))
    return [n for n in out if prefix is None or n.startswith(prefix)]

def env(x, hop=HOP):
    n = len(x) // hop; return np.abs(x[:n * hop]).reshape(n, hop).mean(axis=1)

def lag_ms(mic, ref, max_ms=400):
    """Delay of the echo in the microphone relative to the reference (cross-correlation of 1 ms envelopes of the first 12 s)."""
    n = min(len(mic), len(ref), 12 * SR); a = env(mic[:n], 16); b = env(ref[:n], 16)
    a = a - a.mean(); b = b - b.mean(); c = signal.correlate(a, b, mode="full", method="fft"); k = np.arange(-len(b) + 1, len(a))
    sel = (k >= 0) & (k <= max_ms); return int(k[sel][np.argmax(c[sel])])

def aec_webrtc(mic, ref, delay_ms, warm=()):
    """warm = [(mic, ref, delay_ms)] earlier playback the canceller has already adapted on (production keeps one instance alive across turns)."""
    from aec_audio_processing import AudioProcessor
    ap = AudioProcessor(enable_aec=True, enable_ns=False, ns_level=0, enable_agc=False, enable_vad=False)
    ap.set_stream_format(SR, 1, SR, 1); ap.set_reverse_stream_format(SR, 1)
    def go(mic, ref, d):
        ap.set_stream_delay(int(d)); m = (np.clip(mic, -1, 1) * 32767).astype(np.int16); r = (np.clip(ref, -1, 1) * 32767).astype(np.int16); out = np.zeros_like(m)
        for i in range(0, len(m) - 159, 160):
            ap.process_reverse_stream(r[i:i + 160].tobytes()); out[i:i + 160] = np.frombuffer(ap.process_stream(m[i:i + 160].tobytes()), dtype=np.int16)
        return out.astype(np.float32) / 32768
    for w in warm: go(*w)
    return go(mic, ref, delay_ms)

def aec_speex(mic, ref, delay_ms, warm=(), filter_ms=250):
    from pyaec import Aec
    aec = Aec(160, int(filter_ms * SR / 1000), SR, True)
    def go(mic, ref, d):
        shift = int(d * SR / 1000); r = np.concatenate([np.zeros(shift, np.float32), ref])[:len(mic)]
        m = (np.clip(mic, -1, 1) * 32767).astype(np.int16); rr = (np.clip(r, -1, 1) * 32767).astype(np.int16); out = np.zeros_like(m)
        for i in range(0, len(m) - 159, 160): out[i:i + 160] = np.asarray(aec.cancel_echo(m[i:i + 160], rr[i:i + 160]), dtype=np.int16)
        return out.astype(np.float32) / 32768
    for w in warm: go(*w)
    return go(mic, ref, delay_ms)

CANCELLERS = {"webrtc": aec_webrtc, "speex": aec_speex}

def erle_db(mic, out, skip=2.0):
    a = mic[int(skip * SR):]; b = out[int(skip * SR):len(mic)]; return 10 * np.log10((a ** 2).sum() / max((b ** 2).sum(), 1e-12))

def aec_report():
    rows = {}
    for n in names("long_"):
        mic, ref = load(n); d = lag_ms(mic, ref); line = {"lag_ms": d, "mic_rms": round(float(np.sqrt((mic ** 2).mean())), 4)}
        for cn, fn in CANCELLERS.items():
            t0 = time.perf_counter(); out = fn(mic, ref, d); line[cn] = {"erle_db": round(float(erle_db(mic, out)), 1), "x_realtime": round((time.perf_counter() - t0) / (len(mic) / SR), 3),
                                                                     "resid_rms": round(float(np.sqrt((out[2 * SR:] ** 2).mean())), 5)}
        rows[n] = line; print(n, json.dumps(line), flush=True)
    return rows


# ---------------------------------------------------------------- scenarios
import difflib, re, random
RESULTS = ROOT / "evals" / "results"; K = 3  # consecutive 20 ms frames over threshold (60 ms) before a duck
STOP = re.compile(r"^\s*(?:(?:hey|okay|ok)[\s,]+)?(?:julius[\s,.:!?-]*)?(?:stop|cancel|wait|enough|quiet|shut up|hold on|pause)\b", re.I)
LONG_TEXT = None

def spoken_text(name):
    sys.path.insert(0, str(ROOT / "diagnostics")); sys.path.insert(0, str(ROOT / "wake"))
    if name.startswith("long_"):
        import re as _re; src = (ROOT / "diagnostics" / "record_echo.py").read_text(); m = _re.search(r"LONG = \[(.*?)\n\]", src, _re.S)
        return eval("[" + m.group(1) + "]")[int(name.split("_")[1])]
    import phrases as P; return P.JULIUS_OWN_EVAL[int(name.split("_")[-1])]

def norm(t): return re.sub(r"[^a-z0-9' ]+", " ", t.lower()).split()
def self_match(heard, spoken):
    """Share of the heard words that occur as an in-order run inside the text Julius is speaking (1.0 = pure self-echo)."""
    h, s = norm(heard), norm(spoken)
    if not h: return 1.0
    sm = difflib.SequenceMatcher(None, h, s, autojunk=False); return sum(b.size for b in sm.get_matching_blocks()) / len(h)

def first_active(x_env, thr, start_frame=0):
    """Index of the frame that completes K consecutive frames above thr (thr scalar or per-frame array), else None."""
    over = x_env > thr; run = 0
    for i in range(start_frame, len(over)):
        run = run + 1 if over[i] else 0
        if run >= K: return i
    return None

def all_events(x_env, thr, gap=50):
    ev, i = [], 0
    while True:
        j = first_active(x_env, thr, i)
        if j is None: return ev
        ev.append(j); i = j + gap

def ref_env_delayed(ref, lag, n):
    d = np.concatenate([np.zeros(int(lag * SR / 1000), np.float32), ref])[:n]; return env(d)

def dry_onset(dry):
    e = env(dry); floor = max(np.median(e[:10]), 1e-4); idx = np.where(e > max(5 * floor, 0.003))[0]
    return int(idx[0]) if len(idx) else None

def warm_for(name, echoes, lags):
    """One earlier long answer (not this clip) the canceller has already heard."""
    w = [n for n in sorted(echoes) if n.startswith("long") and n != name][0]; return [(echoes[w][0], echoes[w][1], lags[w])]

class Cal:
    """Thresholds from warm echo-only residuals of the long answers, leaving out the clip being scored."""
    def __init__(self, echoes, lags):
        self.rows = {}
        for n in echoes:
            if not n.startswith("long"): continue
            mic, ref = echoes[n]; e = env(mic); r = ref_env_delayed(ref, lags[n], len(mic)); ok = (r > 0.01)[:len(e)]
            self.rows[n] = {"gains": list(e[:len(r)][ok] / r[ok]), **{c: list(env(fn(mic, ref, lags[n], warm_for(n, echoes, lags)))[150:]) for c, fn in CANCELLERS.items()}}
    def get(self, name):
        use = [v for k, v in self.rows.items() if k != name]
        return {"G": float(np.percentile(sum((u["gains"] for u in use), []), 95)), **{c: 1.5 * float(np.percentile(sum((u[c] for u in use), []), 99.5)) for c in CANCELLERS}}

def signals(mic, ref, lag, warm):
    return {"raw": mic, "level": mic, "webrtc": aec_webrtc(mic, ref, lag, warm), "speex": aec_speex(mic, ref, lag, warm), "webrtc+level": None}

def triggers(opt, sig, mic, ref, lag, cal, floor):
    """Frames at which each option ducks Julius (list of frame indices), given its cleaned signal."""
    e_mic = env(mic); n = len(e_mic); r = ref_env_delayed(ref, lag, len(mic))[:n]
    if opt == "raw": return all_events(e_mic, 3 * floor)
    expected = cal["G"] * r
    if opt == "level": return all_events(e_mic, 2 * expected + 3 * floor)
    e = env(sig)[:n]
    if opt in CANCELLERS: return all_events(e, max(cal[opt], 3 * floor))
    w = env(sig)[:n]; thr = np.maximum(cal["webrtc"], 3 * floor)
    return [j for j in all_events(w, thr) if e_mic[j] > 1.5 * expected[j] + 3 * floor]  # AEC residual AND mic well above the expected echo

OPTIONS = ["raw", "level", "webrtc", "speex", "webrtc+level"]

def make_verifier():
    import mlx_whisper; import mlx.core as mx; from julius import config
    from julius.audio.listening import has_wake
    from julius.audio.speech_text import fix_transcript
    hint = config.asr_initial_prompt(wake=True); repo = "mlx-community/whisper-small.en-mlx"
    def tr(x):
        mx.random.seed(1301); return fix_transcript(mlx_whisper.transcribe(x.astype(np.float32), path_or_hf_repo=repo, condition_on_previous_text=False, language="en",
                                                                         no_speech_threshold=0.6, initial_prompt=hint)["text"].strip())
    tr(np.zeros(SR * 2, np.float32)); return tr

def decide(text, spoken, use_b):
    """Permanent stop? Wake word or stop command in the transcript, unless it is the same text Julius is saying (option b)."""
    from julius.audio.listening import has_wake
    cmd = has_wake(text) or bool(STOP.match(text))
    return cmd and not (use_b and self_match(text, spoken) >= 0.8)

def window(sig, j, back=0.3, fwd=1.5):
    s = max(0, int((j - K) * HOP - back * SR)); return sig[s:s + int((back + fwd) * SR)]

def dry_pool():
    stops = [f for f in sorted((REAL / "stop").glob("*.wav"))]; quick = sorted((REAL / "quick").glob("*.wav"))[:20]
    qp = json.load(open(REAL / "quick" / "prompts.json")); sp = json.load(open(REAL / "stop" / "prompts.json"))
    cmds = [(f, sp[int(f.stem.split("_")[1]) - 1][0]) for f in stops] + [(f, qp[int(f.stem.split("_")[1]) - 1][0]) for f in quick]
    rest = []
    for s in ("talk1", "talk2", "talk3", "talk4"):
        tp = json.load(open(REAL / s / "prompts.json"))
        for i, (t, _) in enumerate(tp):
            if re.search(r"\b(julia|julian|julie|jules)\b", t, re.I): rest.append((REAL / s / f"{s}_{i+1:02d}.wav", t))
    return cmds, rest

def mix(echo_mic, dry, off, gain):
    out = echo_mic.copy(); seg = dry[:max(0, len(out) - off)] * gain; out[off:off + len(seg)] += seg; return out

def run():
    tr = make_verifier()
    cmds, juls = dry_pool(); rng = random.Random(7); echoes = {n: load(n) for n in names()}; lags = {n: lag_ms(*echoes[n]) for n in echoes}
    longs = [n for n in echoes if n.startswith("long")]; cal = Cal(echoes, lags); out = {"i": {}, "ii": [], "iii": []}
    # (i) silence
    for n in echoes:
        mic, ref = echoes[n]; floor = max(float(np.median(env(mic[:int(.4 * SR)]))), 1e-4); sg = signals(mic, ref, lags[n], warm_for(n, echoes, lags)); spoken = spoken_text(n); c = cal.get(n)
        for o in OPTIONS:
            ev = triggers(o, sg[o if o != "webrtc+level" else "webrtc"], mic, ref, lags[n], c, floor)[:6]; row = out["i"].setdefault(o, {"clips": 0, "ducks": 0, "ducks_after_3s": 0, "clips_ducked_after_3s": 0, "stops": 0, "stops_with_b": 0, "clips_ducked": 0})
            row["clips"] += 1; row["ducks"] += len(ev); row["clips_ducked"] += bool(ev); late = [j for j in ev if j >= 150]; row["ducks_after_3s"] += len(late); row["clips_ducked_after_3s"] += bool(late)
            for j in ev:
                text = tr(window(sg[o if o != "webrtc+level" else "webrtc"], j)); row["stops"] += decide(text, spoken, False); row["stops_with_b"] += decide(text, spoken, True)
        print("(i)", n, flush=True)
    # (ii)/(iii)
    def case(file, text, kind, k):
        dry = sf.read(file, dtype="float32")[0]; on = dry_onset(dry)
        if on is None: return
        n = longs[k % len(longs)]; mic, ref = echoes[n]; hi = len(mic) / SR - len(dry) / SR - 1.0
        off0 = int(rng.uniform(4.0, hi) * SR)
        for gain in (1.0, 0.5):
            off = off0; m = mix(mic, dry, off, gain); onset_f = off // HOP + on; floor = max(float(np.median(env(mic[:int(.4 * SR)]))), 1e-4)
            sg = signals(m, ref, lags[n], warm_for(n, echoes, lags)); spoken = spoken_text(n); c = cal.get(n)
            for o in OPTIONS:
                s = sg[o if o != "webrtc+level" else "webrtc"]; allt = triggers(o, s, m, ref, lags[n], c, floor); ev = [j for j in allt if j >= onset_f]; early = [j for j in allt if 150 <= j < onset_f - 3]  # triggers after the 3 s adaptation period and before the user speaks
                rec = {"file": file.name, "text": text, "echo": n, "gain": gain, "option": o, "false_before_onset": len(early), "duck_ms": None, "stop": None, "stop_b": None, "heard": None}
                if ev:
                    rec["duck_ms"] = round((ev[0] - onset_f) * HOP / SR * 1000); heard = tr(window(s, ev[0])); rec["heard"] = heard; rec["stop"] = bool(decide(heard, spoken, False)); rec["stop_b"] = bool(decide(heard, spoken, True))
                out[kind].append(rec)
    for k, (f, t) in enumerate(cmds): case(f, t, "ii", k); print("(ii)", f.name, flush=True)
    for k, (f, t) in enumerate(juls): case(f, t, "iii", k); print("(iii)", f.name, flush=True)
    RESULTS.mkdir(exist_ok=True); p = RESULTS / "bargein_isolated.json"; p.write_text(json.dumps(out, indent=1)); print("wrote", p)
    summarize(out)

def summarize(out):
    print("\n(i) Julius speaks, user silent: clips ducked / permanent stops (without b / with b), of", out["i"]["raw"]["clips"], "clips")
    for o, r in out["i"].items(): print(f"  {o:14s} ducked clips {r['clips_ducked']:2d}  duck events {r['ducks']:3d} (after 3 s: {r['ducks_after_3s']:2d} in {r['clips_ducked_after_3s']:2d} clips)  stops {r['stops']:2d}  stops+b {r['stops_with_b']:2d}")
    for kind, title in (("ii", "(ii) 'Julius, stop' / questions over the echo"), ("iii", "(iii) Julia/Julian/Julie/Jules sentences over the echo")):
        print("\n" + title)
        for o in OPTIONS:
            rs = [r for r in out[kind] if r["option"] == o]; d = [r["duck_ms"] for r in rs if r["duck_ms"] is not None]
            line = f"  {o:14s} cases {len(rs):3d} ducked {len(d):3d}"
            if d: line += f" duck_ms median {int(np.median(d))} p95 {int(np.percentile(d, 95))} within300 {sum(x <= 300 for x in d)}"
            line += f" false-before-onset {sum(r['false_before_onset'] > 0 for r in rs)}  stops {sum(bool(r['stop']) for r in rs)}  stops+b {sum(bool(r['stop_b']) for r in rs)}"; print(line)


# ---------------------------------------------------------------- real double-talk (diagnostics/record_doubletalk.py)
DT = ROOT / "wake" / "data" / "doubletalk"

def persistent(kind):
    """One canceller instance kept alive across takes (as in production): returns go(mic, ref, delay_ms)."""
    if kind == "webrtc":
        from aec_audio_processing import AudioProcessor
        ap = AudioProcessor(enable_aec=True, enable_ns=False, ns_level=0, enable_agc=False, enable_vad=False); ap.set_stream_format(SR, 1, SR, 1); ap.set_reverse_stream_format(SR, 1)
        def go(mic, ref, d):
            ap.set_stream_delay(int(d)); m = (np.clip(mic, -1, 1) * 32767).astype(np.int16); r = (np.clip(ref, -1, 1) * 32767).astype(np.int16); out = np.zeros_like(m)
            for i in range(0, len(m) - 159, 160):
                ap.process_reverse_stream(r[i:i + 160].tobytes()); out[i:i + 160] = np.frombuffer(ap.process_stream(m[i:i + 160].tobytes()), dtype=np.int16)
            return out.astype(np.float32) / 32768
        return go
    from pyaec import Aec
    aec = Aec(160, int(250 * SR / 1000), SR, True)
    def go(mic, ref, d):
        r = np.concatenate([np.zeros(int(d * SR / 1000), np.float32), ref])[:len(mic)]; m = (np.clip(mic, -1, 1) * 32767).astype(np.int16); rr = (np.clip(r, -1, 1) * 32767).astype(np.int16); out = np.zeros_like(m)
        for i in range(0, len(m) - 159, 160): out[i:i + 160] = np.asarray(aec.cancel_echo(m[i:i + 160], rr[i:i + 160]), dtype=np.int16)
        return out.astype(np.float32) / 32768
    return go

def take_list():
    out = []
    for kind in ("stop", "question", "julia"):
        for p in sorted(DT.glob(f"{kind}_*_mic.wav")):
            n = p.name[:-8]; mic, _ = sf.read(DT / f"{n}_mic.wav", dtype="float32"); ref, _ = sf.read(DT / f"{n}_ref.wav", dtype="float32"); k = min(len(mic), len(ref))
            out.append((n, json.loads((DT / f"{n}.json").read_text()), mic[:k], ref[:k]))
    return out

def annotate_onset(res):
    """Energy annotation of when the user starts: the first run of 3 frames after 3 s that rises well above this take's own residual level (an annotation, not human-labelled; 3 takes where nobody spoke are reported as skipped)."""
    e = np.convolve(env(res), np.ones(3) / 3, mode="same"); base = e[30:60]; thr = max(5 * float(np.median(base)), 0.0012, 1.5 * float(np.percentile(base, 95))); return first_active(e, thr, 60)  # baseline: 0.6-1.2 s, before anyone has spoken

def stage1_fires(clean, onset_f, thr_list=(0.05, 0.2, 0.5)):
    """Highest stage-1 'Julius' score in the 2 s after the duck and the time it is first above each threshold (the detector alone, no Whisper)."""
    from julius.wake.stream import WakeStream
    st = WakeStream(str(ROOT / "wake" / "models" / "julius.onnx"), 2.0, verify=None, gate=False); best, first = 0.0, {str(t): None for t in thr_list}
    seg = clean[max(0, onset_f * HOP - int(1.5 * SR)):onset_f * HOP + int(2.5 * SR)]; x = (np.clip(seg, -1, 1) * 32767).astype(np.int16); t0 = onset_f * HOP - max(0, onset_f * HOP - int(1.5 * SR))
    for i in range(0, len(x) - 1279, 1280):
        sc = float(st.model.predict(x[i:i + 1280])[st.name]); best = max(best, sc)
        for t in thr_list:
            if first[str(t)] is None and sc >= t: first[str(t)] = round((i + 1280 - t0) / SR * 1000)
    return best, first

def real():
    tr = make_verifier(); takes = take_list(); print(len(takes), "double-talk takes", flush=True)
    warm = [(n, *load(n)) for n in names("long_")]; lags = {n: lag_ms(m, r) for n, m, r in warm}; rows = []
    cancellers = {c: persistent(c) for c in ("webrtc", "speex")}
    for c, go in cancellers.items():  # production-like: the canceller has already heard earlier answers
        for n, m, r in warm: go(m, r, lags[n])
    for n, meta, mic, ref in takes:
        lag = lag_ms(mic[:6 * SR], ref[:6 * SR]); floor = max(float(np.median(env(mic[:int(.4 * SR)]))), 1e-4); spoken = spoken_text("long_%d" % meta["long"]); res = {c: go(mic, ref, lag) for c, go in cancellers.items()}
        onset = annotate_onset(res["webrtc"]); onset2 = annotate_onset(res["speex"])
        if onset is None or onset2 is None: rows.append({"take": n, "kind": meta["kind"], "text": meta["text"], "skipped": "no user speech found"}); print("skip", n); continue
        onset = min(onset, onset2); e_mic = env(mic); n_f = len(e_mic); r_env = ref_env_delayed(ref, lag, len(mic))[:n_f]
        cal = Cal(warm_echoes := {k: load(k) for k in names("long_")}, {k: lag_ms(*warm_echoes[k]) for k in warm_echoes}).get("none") if not hasattr(real, "cal") else real.cal; real.cal = cal
        for o in ("level", "webrtc", "speex"):
            sig = mic if o == "level" else res[o]; allt = triggers(o, sig, mic, ref, lag, cal, floor); early = [j for j in allt if 150 <= j < onset - 3]; ev = [j for j in allt if j >= onset - 3]
            row = {"take": n, "kind": meta["kind"], "text": meta["text"], "option": o, "onset_s": round(onset * HOP / SR, 2), "false_before_onset": len(early), "duck_ms": None, "stage1": None, "whisper": None, "stop": None}
            if ev:
                row["duck_ms"] = round((ev[0] - onset) * HOP / SR * 1000); win = window(sig, ev[0]); heard = tr(win); row["whisper"] = heard; row["stop"] = bool(decide(heard, spoken, True))
                best, first = stage1_fires(sig, ev[0]); row["stage1"] = {"best": round(best, 3), "first_ms": first}
            rows.append(row)
        print("take", n, flush=True)
    RESULTS.mkdir(exist_ok=True); (RESULTS / "bargein_real.json").write_text(json.dumps(rows, indent=1)); summarize_real(rows)

def summarize_real(rows):
    print("\nreal double-talk (annotated onset; duck = energy trigger on the cleaned signal; confirm = permanent stop)")
    for kind, want in (("stop", True), ("question", True), ("julia", False)):
        print(f" {kind}  (should {'interrupt' if want else 'NOT interrupt'})")
        for o in ("level", "webrtc", "speex"):
            rs = [r for r in rows if r.get("option") == o and r["kind"] == kind]; d = [r["duck_ms"] for r in rs if r["duck_ms"] is not None]
            line = f"   {o:7s} takes {len(rs):2d} ducked {len(d):2d}"
            if d: line += f" duck_ms median {int(np.median(d))} within300 {sum(x <= 300 for x in d)}"
            s1 = [r for r in rs if r["stage1"]]; line += f" | false duck before speech {sum(r['false_before_onset'] > 0 for r in rs)} | whisper stop {sum(bool(r['stop']) for r in rs)}"
            for t in ("0.05", "0.2", "0.5"):
                line += f" | stage1>= {t}: {sum(r['stage1']['first_ms'][t] is not None for r in s1)}"
            print(line)

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "aec"
    if cmd == "summary": summarize(json.load(open(RESULTS / "bargein_isolated.json")))
    else: {"aec": aec_report, "run": run, "real": real}[cmd]()
