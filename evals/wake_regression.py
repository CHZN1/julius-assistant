"""Whisper wake-path regression on your recordings (local only): the production transcription hint plus the wake regex.

  must NOT wake: every Julia / Julian / Julie / Jules / jealous sentence in talk1-talk4 (a bare name-alike must not be heard as Julius)
  must wake:     every recording in the wake sessions (wake, wake2, wake3 ...)
Free to run. Writes evals/results/wake_<time>.csv and .summary.json (ok_pct, newly_failing vs the previous run)."""
import csv, json, re, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np, soundfile as sf, mlx_whisper, mlx.core as mx
from julius_speech_text import fix_transcript
from julius_listening import has_wake
import julius_config as cfg
REAL = ROOT / "diagnostics" / "wake_real"; RESULTS = ROOT / "evals" / "results"
NAMES = re.compile(r"julia|julian|jealous|julie|jules", re.I)

def main():
    hint = cfg.asr_initial_prompt(wake=True); repo = "mlx-community/whisper-small.en-mlx"
    def tr(a):
        mx.random.seed(1301)
        return fix_transcript(mlx_whisper.transcribe(a, path_or_hf_repo=repo, condition_on_previous_text=False, language="en", no_speech_threshold=0.6,
                                                     **({"initial_prompt": hint} if hint else {}))["text"].strip())
    cases = []
    for d in sorted(REAL.glob("talk*")):
        prompts = json.loads((d / "prompts.json").read_text())
        for i, p in enumerate(sorted(d.glob(f"{d.name}_*.wav"))):
            if NAMES.search(prompts[i][0]): cases.append((f"{d.name}/{p.stem}", p, False, prompts[i][0]))
    for d in sorted(REAL.glob("wake*")):
        if d.is_dir(): cases += [(f"{d.name}/{p.stem}", p, True, "") for p in sorted(d.glob("wake*_*.wav"))]
    if not cases: sys.exit("no recordings in diagnostics/wake_real")
    tr(np.zeros(16000, np.float32)); rows = []
    for cid, p, want, text in cases:
        x = sf.read(p, dtype="float32")[0]; heard = tr(x); rows.append({"id": cid, "must_wake": want, "heard": heard, "woke": has_wake(heard), "ok": has_wake(heard) == want})
    stamp = time.strftime("%Y%m%d_%H%M%S"); RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / f"wake_{stamp}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    prev = sorted(RESULTS.glob("wake_*.csv"))[:-1]; was = {r["id"]: r["ok"] == "True" for r in csv.DictReader(open(prev[-1]))} if prev else {}
    fail = [r["id"] for r in rows if not r["ok"]]
    nm = [r for r in rows if not r["must_wake"]]; wk = [r for r in rows if r["must_wake"]]
    summary = {"suite": "wake_regression", "date": time.strftime("%Y-%m-%d %H:%M"), "cases": len(rows), "ok_pct": round(100 * sum(r["ok"] for r in rows) / len(rows), 1),
               "false_wakes": sum(r["woke"] for r in nm), "name_sentences": len(nm), "wake_misses": sum(not r["woke"] for r in wk), "wake_recordings": len(wk),
               "failing": fail, "regressions_vs_previous": {"newly_failing": [i for i in fail if was.get(i, True)]} if prev else {}, "total_usd": 0.0}
    (RESULTS / f"wake_{stamp}.summary.json").write_text(json.dumps(summary, indent=1))
    print(f"wake regression: {summary['ok_pct']}% ({summary['false_wakes']} false wakes of {len(nm)} name sentences, {summary['wake_misses']} misses of {len(wk)} wake recordings)")
    for r in rows:
        if not r["ok"]: print("  FAIL", r["id"], "must wake" if r["must_wake"] else "must NOT wake", "|", r["heard"][:70])
    return 0

if __name__ == "__main__":
    sys.exit(main())
