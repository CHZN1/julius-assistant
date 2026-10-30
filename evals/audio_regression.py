"""Recorded-audio regression suite (no speakers, no microphone).

Replays your 20 recorded phrases (diagnostics/asr_real/phrase_NN.wav) plus the standard typed test prompts:
  1. ASR: mlx-whisper small.en exactly as Julius calls it, then fix_transcript; scored as WER and missed key terms.
  2. Router -> brain -> tools -> TTS: the transcripts are fed to a headless cloud-profile Julius (JULIUS_VALIDATION_NO_DEVICES, the real
     MLX voice synthesises but nothing plays); per turn it records the path taken (shortcut/direct/tool/escalate), the tools called,
     time to first audio, brain first-token time and cost.
Estimated end-of-speech-to-first-audio for a recorded phrase = silence wait (0.6 s) + ASR time + processing-to-first-audio.
It spends real money on the cloud brain (typically 10-40 cents, mostly the planner turns).

Usage: python evals/audio_regression.py [--dir diagnostics/asr_real] [--no-pipeline]
"""
import argparse, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
from routing_eval import judge

CASES = json.loads((Path(__file__).parent / "audio_cases.json").read_text())
SILENCE_WAIT = 0.6
SEED = 1301
JOIN = [(r"\bprize picks\b", "acmecorp"), (r"\bn h l\b", "nhl"), (r"\bn f l\b", "nfl"), (r"\bp\.? ?m\b\.?", "pm"), (r"\ba\.? ?m\b\.?", "am")]

def norm(text):  # same normalisation as diagnostics/bench_asr_real.py
    t = text.lower().replace("%", " percent ").replace("-", " ")
    t = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", " ", t); t = re.sub(r"(?<=\d),(?=\d)", "", t)
    t = re.sub(r"(\d):(\d)", r"\1 \2", t); t = re.sub(r"(?<=\d)\.(?=\d)", " ", t)
    for pattern, replacement in JOIN: t = re.sub(pattern, replacement, t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", t)).strip()

def wer(ref, hyp):
    r, h = norm(ref).split(), norm(hyp).split(); d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1])); prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))

def missed(number, transcript):
    n = " " + norm(transcript) + " "
    return [kw for kw in CASES["keywords"].get(str(number), []) if not any(f" {alt} " in n for alt in kw.split("|"))]

def run_asr(folder):
    import numpy as np, soundfile as sf, mlx_whisper, mlx.core as mx
    from julius_speech_text import fix_transcript
    import julius_config
    hint = julius_config.asr_initial_prompt()  # same vocabulary hint as the live transcriber
    repo = "mlx-community/whisper-small.en-mlx"
    phrases = json.loads((folder / "phrases.json").read_text())
    clips = sorted(folder.glob("phrase_*.wav"))
    assert clips, f"no phrase_*.wav in {folder}"
    def transcribe(path):
        audio, _ = sf.read(path, dtype="float32")
        audio = audio[:, 0] if audio.ndim > 1 else audio
        mx.random.seed(SEED)  # Whisper falls back to sampled decoding on hard segments; seeding makes runs repeatable
        t0 = time.perf_counter()
        r = mlx_whisper.transcribe(audio, path_or_hf_repo=repo, condition_on_previous_text=False, language="en", no_speech_threshold=0.6, **({"initial_prompt": hint} if hint else {}))
        return fix_transcript(r["text"].strip()), time.perf_counter() - t0
    transcribe(clips[0])  # warm-up: model load is not part of a turn
    out = []
    for p in clips:
        n = int(p.stem.split("_")[1]); text, secs = transcribe(p)
        out.append({"n": n, "reference": phrases[n - 1], "transcript": text, "asr_s": secs})
    return out

def run_pipeline(texts):
    work = Path(tempfile.mkdtemp(prefix="julius_audio_eval_"))
    (work / "script.json").write_text(json.dumps(texts))
    env = dict(os.environ, JULIUS_PROFILE="cloud", JULIUS_VALIDATION_NO_DEVICES="1", JULIUS_VALIDATION_SCRIPT=str(work / "script.json"),
               JULIUS_VALIDATION_OUTPUT=str(work / "turns.json"), JULIUS_MEMORY_DIR=str(work / "mem"), JULIUS_COSTS_FILE=str(work / "costs.jsonl"))
    t0 = time.time()
    proc = subprocess.run([str(ROOT / "run_julius.sh")], cwd=ROOT, env=env, capture_output=True, text=True, timeout=2400)
    (work / "out.log").write_text(proc.stdout + proc.stderr)
    turns = json.loads((work / "turns.json").read_text()) if (work / "turns.json").exists() else []
    return turns, proc.returncode, time.time() - t0, work

def classify(turn):
    """Path from the turn record: a brain call shows up as cost (shortcuts and file/utility handlers spend nothing)."""
    tools = [t.get("tool") for t in turn.get("dispatcher_tasks") or []]
    brain = bool(turn.get("cost_by_tier_usd"))
    failed = any(t.get("status") == "failed" for t in turn.get("dispatcher_tasks") or [])
    if "escalate_to_planner" in tools: return {"path": "escalate", "tool": "escalate_to_planner", "ok": not turn.get("error")}
    if tools:
        label = {"human_file": "files", "human_planning": "utility"}.get(tools[0], tools[0])
        return {"path": "tool" if brain else "shortcut", "tool": label, "ok": not failed}
    return {"path": "direct" if brain else "shortcut", "tool": "", "ok": not turn.get("error")}

def repeat_stats(runs, items, rows):
    """Median first audio and mean cost per turn by the path actually taken, plus Haiku cache use, over every pipeline run."""
    if not runs: return {}
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    per = {}
    for turns, *_ in runs:
        for turn in turns:
            c = classify(turn)
            first = turn.get("processing_to_first_audio_seconds")
            per.setdefault(c["path"], []).append((first, sum((turn.get("cost_by_tier_usd") or {}).values())))
    by_actual = {p: {"n": len(v), "first_audio_median_s": None if not [f for f, _ in v if f is not None] else round(med([f for f, _ in v if f is not None]), 2),
                     "cost_mean_usd": round(sum(c for _, c in v) / len(v), 5)} for p, v in per.items()}
    read = write = fresh = calls = 0
    for _, _, _, work in runs:
        f = work / "costs.jsonl"
        for line in (f.read_text().splitlines() if f.exists() else []):
            r = json.loads(line)
            if r.get("tier") == "haiku":
                read += r["cache_read"]; write += r["cache_write"]; fresh += r["input"]; calls += 1
    total_turns = sum(len(t) for t, *_ in runs)
    per_run_cost = [sum(sum((t.get("cost_by_tier_usd") or {}).values()) for t in turns) / max(1, len(turns)) for turns, *_ in runs]
    return {"repeats": len(runs), "by_actual_path": by_actual, "cost_per_turn_median_usd": round(med(per_run_cost), 5),
            "haiku_calls": calls, "haiku_cache_hit_pct": round(100 * read / max(1, read + write + fresh), 1),
            "haiku_cache_write_tokens_per_call": round(write / max(1, calls)), "haiku_cache_read_tokens_per_call": round(read / max(1, calls)), "haiku_uncached_tokens_per_call": round(fresh / max(1, calls))}

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default=str(ROOT / "diagnostics" / "asr_real")); ap.add_argument("--no-pipeline", action="store_true", help="ASR scoring only (free)")
    ap.add_argument("--repeats", type=int, default=1, help="run the headless pipeline this many times; latency and cost are medians over the runs")
    a = ap.parse_args()
    asr = run_asr(Path(a.dir))
    items = [{"id": c["id"], "kind": "typed", "reference": c["text"], "transcript": c["text"], "asr_s": 0.0, "expect": c["expect"], "n": None} for c in CASES["typed"]]
    items += [{"id": f"p{x['n']:02d}", "kind": "recorded", "reference": x["reference"], "transcript": x["transcript"], "asr_s": x["asr_s"], "expect": CASES["recorded"][str(x["n"])]["expect"], "n": x["n"]} for x in asr]
    runs = [] if a.no_pipeline else [run_pipeline([i["transcript"] for i in items]) for _ in range(max(1, a.repeats))]
    turns, code, seconds, work = runs[-1] if runs else ([], None, 0, None)
    rows = []
    for k, item in enumerate(items):
        turn = turns[k] if k < len(turns) else None
        got = classify(turn) if turn else {"path": "missing", "tool": "", "ok": False}
        w = wer(item["reference"], item["transcript"]) if item["kind"] == "recorded" else None
        miss = missed(item["n"], item["transcript"]) if item["kind"] == "recorded" else []
        case = {"expect": {k: v for k, v in item["expect"].items() if k != "args_contain"}}  # dispatcher records carry no arguments
        p, t, _, s = judge(case, got) if turn else (False, False, True, False)
        cost = sum((turn.get("cost_by_tier_usd") or {}).values()) if turn else 0.0
        first = turn.get("processing_to_first_audio_seconds") if turn else None
        rows.append({"id": item["id"], "kind": item["kind"], "reference": item["reference"], "transcript": item["transcript"], "wer": None if w is None else round(w, 3),
                     "missed_terms": "|".join(miss), "asr_s": round(item["asr_s"], 3), "expected_path": item["expect"]["path"],
                     "expected_tool": item["expect"].get("tool") or "|".join(item["expect"].get("tool_in", [])), "actual_path": got["path"], "actual_tool": got["tool"],
                     "path_ok": p, "tool_ok": t, "asr_ok": (w is not None and not miss) if item["kind"] == "recorded" else True,
                     "ok": bool(p and t and s and (not miss) and not (turn or {}).get("error")),
                     "first_audio_s": None if first is None else round(first, 2),
                     "est_end_of_speech_to_first_audio_s": None if first is None or item["kind"] != "recorded" else round(SILENCE_WAIT + item["asr_s"] + first, 2),
                     "brain_ttft_s": None if not turn or not turn.get("brain_first_token_seconds") else round(turn["brain_first_token_seconds"], 2),
                     "turn_s": None if not turn else round(turn["seconds"], 2), "usd": round(cost, 5), "error": (turn or {}).get("error") or "", "answer": ((turn or {}).get("answer") or "")[:140].replace("\n", " ")})
    rec = [r for r in rows if r["kind"] == "recorded"]; pipe = [r for r in rows if r["turn_s"] is not None]
    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None
    firsts = sorted(r["first_audio_s"] for r in pipe if r["first_audio_s"] is not None)
    summary = {"suite": "audio_regression", "date": time.strftime("%Y-%m-%d %H:%M"), "recorded_phrases": len(rec), "typed_prompts": len(rows) - len(rec),
               "mean_wer": mean([r["wer"] for r in rec]), "phrases_with_missed_terms": sum(1 for r in rec if r["missed_terms"]), "mean_asr_s": mean([r["asr_s"] for r in rec]),
               "pipeline_ran": bool(turns), "pipeline_exit_code": code, "pipeline_seconds": round(seconds, 1),
               "routing_path_ok_pct": pct([r["path_ok"] for r in pipe]), "routing_tool_ok_pct": pct([r["tool_ok"] for r in pipe]), "turn_ok_pct": pct([r["ok"] for r in rows]),
               "first_audio_median_s": firsts[len(firsts) // 2] if firsts else None, "first_audio_max_s": firsts[-1] if firsts else None,
               "est_end_of_speech_to_first_audio_mean_s": mean([r["est_end_of_speech_to_first_audio_s"] for r in rec if r["est_end_of_speech_to_first_audio_s"]]),
               "cost_per_turn_mean_usd": round(sum(r["usd"] for r in pipe) / max(1, len(pipe)), 5), "total_usd": round(sum(r["usd"] for r in pipe), 4),
               "by_path": {p: {"n": sum(1 for r in pipe if r["expected_path"] == p), "first_audio_median_s": (lambda xs: xs[len(xs) // 2] if xs else None)(sorted(r["first_audio_s"] for r in pipe if r["expected_path"] == p and r["first_audio_s"] is not None)),
                               "cost_mean_usd": round(sum(r["usd"] for r in pipe if r["expected_path"] == p) / max(1, sum(1 for r in pipe if r["expected_path"] == p)), 5)} for p in ("shortcut", "direct", "tool", "escalate")}}
    summary.update(repeat_stats(runs, items, rows))
    path, previous = common.write_results("audio", rows, summary)
    broke, fixed, vs = common.regressions(rows, previous)
    summary["regressions_vs_previous"] = {"previous": vs, "newly_failing": broke, "newly_passing": fixed}
    path.with_suffix(".summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "by_path"}, indent=1)); print("by_path:", json.dumps(summary["by_path"]))
    for r in rows:
        if not r["ok"]: print(f"  MISS {r['id']} [{r['kind']}] {r['transcript'][:60]!r}: expected {r['expected_path']}/{r['expected_tool']} got {r['actual_path']}/{r['actual_tool']}"
                              + (f" missed={r['missed_terms']}" if r["missed_terms"] else "") + (f" error={r['error']}" if r["error"] else ""))
    print(f"results: {path.name}" + (f"; work dir {work}" if work else ""))
    return 1 if broke else 0

if __name__ == "__main__":
    sys.exit(main())
