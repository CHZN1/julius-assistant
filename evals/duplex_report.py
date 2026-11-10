"""Report on the duplex live test (evals/voice_record.py record --duplex d1 d2 d3).

    python evals/duplex_report.py            latest take of d1, d2, d3
    python evals/duplex_report.py --all-takes

Measured per take, from the recording (events, mic.wav = raw microphone, out.wav = what was played, both on the same clock):
  self-echo        utterances the recognizer produced from Julius's own voice (text overlapping what he said), and how many of them became a turn he answered
  barge-in         for each "talk over him" instruction: did he stop (talk_over event), how long after you started, and was the second sentence understood and answered
  false interrupts talk_over events during the "stay silent" stretches (his own voice, or noise, stopped him)
  fallback         whether the echo canceller was judged broken and the mic gate came back
  echo reduction   microphone level during his speech before and after cancellation (dB), on stretches where you were silent
Pass line (what to look at before making duplex the default): self-echo turns 0, false interrupts 0, every normal-voice barge-in stops within 1.5 s, no fallback."""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import voice_eval as ve

from julius.audio import selfecho

SR = 16000


def wav(path):
    import wave

    with wave.open(str(path), "rb") as w:
        return (
            np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32767
        )


def _self_echo(events):
    """(utterances recognised from his own voice, how many of them became a turn he answered)."""
    spoken = " ".join(e.get("text", "") for e in events if e["kind"] == "reply" and e.get("text"))
    heard = [
        e
        for e in events
        if e["kind"] == "asr"
        and e.get("text")
        and selfecho.overlap(e["text"], spoken) >= 0.6
        and len(e["text"].split()) >= 3
    ]
    turned = [
        e for e in heard if any(t["kind"] == "turn" and 0 <= t["t"] - e["t"] < 3 for t in events)
    ]
    return heard, turned


def _barge_entry(scenario, lab, win, talk, rows):
    """One talk-over instruction: did he stop, how fast, and was the sentence understood."""
    r = next((x for x in rows if x["turn"] == lab["turn"]), None)
    said = scenario["turns"][lab["turn"] + 1]
    heard_req = next((e["t"] for e in win if e["kind"] == "asr"), None)
    return {
        "say": re.sub(r"\s*\[[^\]]*\]\s*", " ", said.get("ref") or said["say"]).strip(),
        "stopped": bool(talk),
        "stop_after_s": round(talk[0]["t"] - heard_req, 1) if talk and heard_req else None,
        "understood": bool(r and r["wer"] is not None and r["wer"] <= 0.5),
        "wer": r and r["wer"],
        "answered": bool(r and r["handled_ok"]),
        "heard": r and r["heard"],
    }


def _echo_reduction_db(take, scenario, duplex):
    """Microphone level while he speaks, before vs after cancellation, in dB (d1 scenarios only)."""
    mic = wav(take / "rec" / "mic.wav")
    ref = wav(take / "rec" / "out.wav")
    n = min(len(mic), len(ref))
    from julius.audio import aec

    cl = aec.cancel(mic[:n], ref[:n], lag=0.0 if duplex else None)

    def level(x):
        return np.array([np.sqrt(np.mean(x[i : i + 1600] ** 2)) for i in range(0, n - 1600, 1600)])

    playing = level(ref) > 0.02
    if not scenario["id"].startswith("d1") or playing.sum() <= 20:
        return None
    idx = np.flatnonzero(playing)[30:]
    r_raw, r_cl = level(mic), level(cl)
    return round(float(20 * np.log10(np.mean(r_raw[idx]) / max(np.mean(r_cl[idx]), 1e-6))), 1)


def report_take(take):
    """Self-echo, barge-in, false-interrupt and echo-reduction figures for one recorded take."""
    sc = json.loads((take / "scenario.json").read_text())
    events = ve.read_jsonl(take / "rec" / "events.jsonl")
    labels = ve.read_jsonl(take / "labels.jsonl")
    rows = ve.score_take(take)
    end_t = next((e["t"] for e in events if e["kind"] == "session_end"), events[-1]["t"]) + 1
    selfheard, turns_from_self = _self_echo(events)
    out = {
        "take": take.name,
        "duplex": any(e["kind"] == "duplex" for e in events),
        "self_heard": len(selfheard),
        "self_heard_turns": len(turns_from_self),
        "fallback": [e.get("reason") for e in events if e["kind"] == "duplex_fallback"],
        "barge": [],
        "false_interrupts": 0,
        "rows": rows,
    }
    for lab, a, b in ve.turn_windows(labels, end_t):
        win = [e for e in events if a <= e["t"] < b]
        talk = [e for e in win if e["kind"] == "talk_over"]
        expect = lab.get("expect") or {}
        if lab.get("group", 1) > 1:
            # the head instruction is the long request; the instruction(s) after it are said over his answer
            out["barge"].append(_barge_entry(sc, lab, win, talk, rows))
        elif expect.get("ignored") or expect.get("kind") == "long_answer":
            out["false_interrupts"] += len(talk)
    # echo reduction while he speaks, on stretches where the user said nothing (the quiet-story scenarios)
    try:
        reduction = _echo_reduction_db(take, sc, out["duplex"])
        if reduction is not None:
            out["echo_reduction_db"] = reduction
    except Exception as e:
        out["echo_reduction_error"] = f"{type(e).__name__}: {e}"
    return out


def print_take(r, total):
    """Print one take's lines and add its counts to the running totals."""
    print(f"\n{r['take']}  (duplex on: {r['duplex']})")
    print(
        f"  self-echo: {r['self_heard']} utterances from his own voice, {r['self_heard_turns']} became a turn"
    )
    print(f"  false interruptions: {r['false_interrupts']}")
    if "echo_reduction_db" in r:
        print(f"  echo reduction while he talked: {r['echo_reduction_db']} dB")
    for b in r["barge"]:
        stopped = (
            f"stopped {b['stop_after_s']} s after your request was heard"
            if b["stopped"]
            else "DID NOT STOP"
        )
        print(
            f"  talk-over {b['say']!r}: {stopped}; heard {b['heard']!r} (WER {b['wer']}), answered: {b['answered']}"
        )
        total["barge_n"] += 1
        total["barge_ok"] += int(b["stopped"] and b["answered"])
    if r["fallback"]:
        print(f"  FALLBACK to the gate: {r['fallback']}")
    for x in r["rows"]:
        if not x["handled_ok"]:
            print(f"  turn {x['turn']} not handled: {x['why']} ({x['say']!r})")
    total["self_heard_turns"] += r["self_heard_turns"]
    total["false"] += r["false_interrupts"]
    total["fallbacks"] += len(r["fallback"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-takes", action="store_true")
    a = ap.parse_args(argv)
    takes = ve.find_takes(ve.DATA, ["d1", "d2", "d3"], a.all_takes)
    if not takes:
        print("no duplex recordings yet: python evals/voice_record.py record --duplex d1 d2 d3")
        return 1
    total = {"self_heard_turns": 0, "false": 0, "barge_ok": 0, "barge_n": 0, "fallbacks": 0}
    for _sid, ts in takes.items():
        for t in ts:
            print_take(report_take(t), total)
    print(
        f"\nTOTAL  self-echo turns {total['self_heard_turns']}  false interruptions {total['false']}  barge-in {total['barge_ok']}/{total['barge_n']}  fallbacks {total['fallbacks']}"
    )
    print(
        "PASS"
        if total["self_heard_turns"] == 0
        and total["false"] == 0
        and total["barge_ok"] == total["barge_n"]
        and total["fallbacks"] == 0
        else "NOT YET: see the lines above"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
