"""Front-end replay for the voice eval: pushes the recorded microphone audio back through capture and recognition, so a change to the front end
(gate, echo cancellation, endpointing) is measured on the same real conversations. Free apart from Whisper time. Run through evals/voice_eval.py replay.

What it does for each take:
  1. mic.wav (everything the microphone heard, including while Julius spoke) and out.wav (what he played, same timeline) are loaded.
  2. mode `gate`: the microphone is dropped while Julius plays and for 0.75 s after (today's behaviour). `none`: no gate. `aec`: echo cancellation, out.wav as the reference.
  3. The result is cut into utterances the way the live loop does (1024-sample blocks, energy threshold, 0.6 s of silence ends one) and each is transcribed with the live ASR model.
  4. Each utterance is classed: the user's (matches what the script told him to say in that stretch), Julius hearing himself (matches what Julius said), or neither.
Scores: the user's words recovered (1 - WER per turn against the script, over the utterances starting in that turn's window), utterances split off, self-heard utterances
(Julius answering himself is the cost of letting audio in during playback), and unexplained utterances."""

import json
import re
import statistics
import sys
import time
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import voice_eval as ve

SR, CHUNK = 16000, 1024
SILENCE_LEN, MIN_SPEECH = 0.6, 0.4
SETTLE = 0.75
PHYS = 0.45  # seconds from a chunk being written to the device until its sound reaches the microphone (device latency 0.4 s + air)
ASR = "mlx-community/whisper-small.en-mlx"


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        return (
            np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32767
        )


def output_mask(out, block=CHUNK, settle=SETTLE, bridge=0.8):
    """Per block: is the microphone gate shut? Playback energy, gaps shorter than `bridge` seconds filled (sentences follow each other), then `settle` more."""
    n = len(out) // block
    active = np.array(
        [np.sqrt(np.mean(out[i * block : (i + 1) * block] ** 2)) > 1e-3 for i in range(n)]
    )
    gate = active.copy()
    last = -(10**9)
    bl = block / SR
    for i in range(n):
        if active[i]:
            if i - last <= bridge / bl:
                gate[last : i + 1] = True
            last = i
    shut = gate.copy()
    k = int(settle / bl)
    for i in np.flatnonzero(gate):
        shut[i : i + k + 1] = True
    return shut[:n]


def noise_threshold(mic):
    quiet = np.sort(
        [np.abs(mic[i : i + CHUNK]).mean() for i in range(0, min(len(mic), SR * 4), CHUNK)]
    )
    floor = float(quiet[len(quiet) // 4]) if len(quiet) else 0.0005
    return min(0.020, max(0.0045, floor * 2.2))


def segment(sig, th, silence=SILENCE_LEN):
    """The live loop's endpointing: [(start_sample, end_sample)] of utterances at least MIN_SPEECH long with two voiced blocks in a row."""
    out, recording, sil, start, voiced, longest = [], False, 0, 0, 0, 0
    bl = CHUNK / SR
    for i in range(len(sig) // CHUNK):
        lvl = float(np.abs(sig[i * CHUNK : (i + 1) * CHUNK]).mean())
        if lvl > th:
            if not recording:
                recording, start, voiced, longest = True, i, 0, 0
            voiced += 1
            longest = max(longest, voiced)
            sil = 0
        elif recording:
            voiced = 0
            sil += 1
            if sil * bl > silence:
                end = i - sil + 1
                if (end - start) * bl >= MIN_SPEECH and longest >= 2:
                    out.append((max(0, start - 8) * CHUNK, min(len(sig), (i + 1) * CHUNK)))
                recording = False
    return out


def front_end(mic, out, mode, duplex=False):
    if mode == "none":
        return mic
    if mode == "gate":
        shut = output_mask(out)
        sig = mic.copy()
        for i in np.flatnonzero(shut):
            sig[i * CHUNK : (i + 1) * CHUNK] = 0
        return sig
    if mode == "aec":
        from julius.audio import aec

        return aec.cancel(
            mic, out, lag=0.0 if duplex else PHYS
        )  # duplex recordings: the reference is already sample-aligned
    raise ValueError(mode)


_asr_ready = False


def transcribe(audio):
    global _asr_ready
    import mlx_whisper

    from julius import config

    prompt = config.asr_initial_prompt()
    r = mlx_whisper.transcribe(
        audio.astype(np.float32),
        path_or_hf_repo=ASR,
        condition_on_previous_text=False,
        language="en",
        no_speech_threshold=0.6,
        **({"initial_prompt": prompt} if prompt else {}),
    )
    nsp = max((s.get("no_speech_prob", 0) for s in r.get("segments", [])), default=0)
    return r["text"].strip(), nsp


def align_streams(events, t0, mic, out, duplex):
    """-> (offset of the mic clock to the event clock, out.wav moved to when chunks were written)."""
    n = len(mic)
    start_ev = next((e for e in events if e["kind"] == "mic_start"), None)
    if duplex and start_ev is not None:
        # duplex recording: mic.wav and out.wav share the callback clock and mic_start gives the offset to the event clock exactly; nothing to estimate
        return start_ev["t"] - t0, out
    end_ev = next((e for e in events if e["kind"] == "session_end"), events[-1])
    # mic.wav is padded to the length of out.wav; the true microphone duration is in the session_end event
    mic_s = end_ev.get("mic_seconds") or n / SR
    # first guess: when the microphone opened relative to session_start (the session closes a little after the last sample, so this is too large)
    off = (end_ev["t"] - t0) - mic_s
    shift = int(round(off * SR))
    if shift > 0:
        out = np.concatenate([out[shift:], np.zeros(shift, dtype=np.float32)])[:n]
    from julius.audio import aec

    lag = aec.estimate_lag(mic, out)  # the acoustic delay (PHYS) plus the error of that guess
    off += lag - PHYS if lag > 0.1 else 0.0
    shift2 = int(round((lag - PHYS) * SR))
    if lag > 0.1 and shift2 != 0:
        # move out.wav so it sits at the moment each chunk was WRITTEN to the device (the live gate's clock); the echo then trails it by PHYS
        if shift2 > 0:
            out = np.concatenate([out[shift2:], np.zeros(shift2, dtype=np.float32)])[:n]
        else:
            out = np.concatenate([np.zeros(-shift2, dtype=np.float32), out])[:n]
    return off, out


def transcribe_segments(sig, threshold):
    """Speech segments of the cleaned signal that Whisper hears as words."""
    segs = []
    for a, b in segment(sig, threshold):
        text, nsp = transcribe(sig[a:b])
        if text and re.search(r"\w", text) and nsp <= 0.6:
            segs.append({"t": a / SR, "end": b / SR, "text": text})
    return segs


def grade_turn(scenario, lab, segs, window, said):
    """One result row: what the user said, what the cleaned front end heard, and the WER."""
    a, b = window
    group = scenario["turns"][lab["turn"] : lab["turn"] + lab.get("group", 1)]
    ignored = (group[0].get("expect") or {}).get("ignored")
    ref = " ".join(re.sub(r"\s*\[[^\]]*\]\s*", " ", t.get("ref") or t["say"]) for t in group)
    mine = [s for s in segs if a <= s["t"] < b]
    selfheard = [
        s for s in mine if ve_overlap(s["text"], said) >= 0.6 and ve.wer(ref, s["text"]) > 0.5
    ]
    roys = [s for s in mine if s not in selfheard]
    hyp = " ".join(s["text"] for s in roys)
    return {
        "scenario": scenario["id"],
        "turn": lab["turn"],
        "say": ref,
        "heard": hyp,
        "wer": None if ignored else round(ve.wer(ref, hyp), 3),
        "utterances": len(roys),
        "expected_utterances": 0 if ignored else len(group),
        "self_heard": len(selfheard),
        "ignored": bool(ignored),
    }


def replay_take(take, mode):
    """Replay one recorded take through the front end: (result rows, heard segments)."""
    scenario = json.loads((take / "scenario.json").read_text())
    events = ve.read_jsonl(take / "rec" / "events.jsonl")
    labels = ve.read_jsonl(take / "labels.jsonl")
    t0 = events[0]["t0"]
    mic = read_wav(take / "rec" / "mic.wav")
    out = read_wav(take / "rec" / "out.wav")
    n = min(len(mic), len(out))
    mic, out = mic[:n], out[:n]
    duplex = any(e["kind"] == "duplex" for e in events)
    off, out = align_streams(events, t0, mic, out, duplex)
    t0 += off
    sig = front_end(mic, out, mode, duplex=duplex)
    segs = transcribe_segments(sig, noise_threshold(mic))
    said = " ".join(e["text"] for e in events if e["kind"] == "reply" and e.get("text"))
    rows = [
        grade_turn(scenario, lab, segs, (a - t0, b - t0), said)
        for lab, a, b in ve.turn_windows(labels, events[-1]["t"] + 1)
    ]
    return rows, segs


def ve_overlap(heard, spoken):
    from julius.audio import selfecho

    return selfecho.overlap(heard, spoken)


def summarize(rows):
    graded = [r for r in rows if r["wer"] is not None]
    return {
        "turns": len(rows),
        "mean_wer": round(statistics.mean(r["wer"] for r in graded), 3) if graded else None,
        "exact": sum(r["wer"] == 0 for r in graded),
        "graded": len(graded),
        "split_or_lost": sum(r["utterances"] != r["expected_utterances"] for r in rows),
        "self_heard": sum(r["self_heard"] for r in rows),
        "silent_turns_answered": sum(r["ignored"] and r["utterances"] > 0 for r in rows),
    }


def main(a):
    takes = ve.find_takes(ve.DATA, [x for x in a.takes.split(",") if x] or None, a.all_takes)
    if not takes:
        print("no recordings")
        return 1
    rows = []
    t_start = time.time()
    for sid, ts in takes.items():
        for t in ts:
            r, _ = replay_take(t, a.mode)
            rows += r
            s = summarize(r)
            print(
                f"{sid:28} mode {a.mode:4} WER {s['mean_wer']}  exact {s['exact']}/{s['graded']}  split/lost {s['split_or_lost']}  self-heard {s['self_heard']}",
                flush=True,
            )
    s = summarize(rows)
    print(
        f"\nfront-end replay, mode {a.mode}: {s['turns']} turns, mean WER {s['mean_wer']}, exact {s['exact']}/{s['graded']}, split or lost {s['split_or_lost']}, self-heard utterances {s['self_heard']}, silent turns answered {s['silent_turns_answered']}  ({time.time() - t_start:.0f}s)"
    )
    ve.RESULTS.mkdir(exist_ok=True)
    (ve.RESULTS / f"voicereplay_{a.mode}_{time.strftime('%Y%m%d_%H%M%S')}.summary.json").write_text(
        json.dumps(
            {
                "suite": "voice_replay",
                "mode": a.mode,
                "date": time.strftime("%Y-%m-%d %H:%M"),
                **s,
                "rows": rows,
            },
            indent=1,
        )
    )
    return 0
