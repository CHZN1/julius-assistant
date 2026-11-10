"""Whisper wake-path regression on your recordings (local only): the production transcription hint plus the wake regex.

  must NOT wake: every Julia / Julian / Julie / Jules / jealous sentence in talk1-talk4 (a bare name-alike must not be heard as Julius)
  must wake:     every recording in the wake sessions (wake, wake2, wake3 ...)
Free to run. Writes evals/results/wake_<time>.csv and .summary.json (ok_pct, newly_failing vs the previous run)."""

import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import mlx.core as mx
import mlx_whisper
import numpy as np
import soundfile as sf

import julius.config as cfg
from julius.audio.listening import has_wake
from julius.audio.speech_text import fix_transcript

REAL = ROOT / "diagnostics" / "wake_real"
RESULTS = ROOT / "evals" / "results"
NAMES = re.compile(r"julia|julian|jealous|julie|jules", re.I)


def make_transcriber():
    """A seeded Whisper transcriber with the wake vocabulary hint."""
    hint = cfg.asr_initial_prompt(wake=True)
    repo = "mlx-community/whisper-small.en-mlx"

    def transcribe(audio):
        mx.random.seed(1301)
        return fix_transcript(
            mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=repo,
                condition_on_previous_text=False,
                language="en",
                no_speech_threshold=0.6,
                **({"initial_prompt": hint} if hint else {}),
            )["text"].strip()
        )

    return transcribe


def collect_cases():
    """(id, wav path, must wake, sentence): name sentences that must not wake, wake recordings that must."""
    cases = []
    for d in sorted(REAL.glob("talk*")):
        prompts = json.loads((d / "prompts.json").read_text())
        for i, p in enumerate(sorted(d.glob(f"{d.name}_*.wav"))):
            if NAMES.search(prompts[i][0]):
                cases.append((f"{d.name}/{p.stem}", p, False, prompts[i][0]))
    for d in sorted(REAL.glob("wake*")):
        if d.is_dir():
            cases += [(f"{d.name}/{p.stem}", p, True, "") for p in sorted(d.glob("wake*_*.wav"))]
    return cases


def run_cases(cases, transcribe):
    transcribe(np.zeros(16000, np.float32))
    rows = []
    for cid, p, want, _text in cases:
        heard = transcribe(sf.read(p, dtype="float32")[0])
        rows.append(
            {
                "id": cid,
                "must_wake": want,
                "heard": heard,
                "woke": has_wake(heard),
                "ok": has_wake(heard) == want,
            }
        )
    return rows


def write_csv(rows, stamp):
    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / f"wake_{stamp}.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def summarize(rows, previous):
    """The summary dict; previous is the earlier run's csv path or None."""
    was = (
        {r["id"]: r["ok"] == "True" for r in csv.DictReader(previous.read_text().splitlines())}
        if previous
        else {}
    )
    fail = [r["id"] for r in rows if not r["ok"]]
    nm = [r for r in rows if not r["must_wake"]]
    wk = [r for r in rows if r["must_wake"]]
    return {
        "suite": "wake_regression",
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "cases": len(rows),
        "ok_pct": round(100 * sum(r["ok"] for r in rows) / len(rows), 1),
        "false_wakes": sum(r["woke"] for r in nm),
        "name_sentences": len(nm),
        "wake_misses": sum(not r["woke"] for r in wk),
        "wake_recordings": len(wk),
        "failing": fail,
        "regressions_vs_previous": {"newly_failing": [i for i in fail if was.get(i, True)]}
        if previous
        else {},
        "total_usd": 0.0,
    }


def main():
    """Replay the real wake recordings through Whisper and report false wakes and misses."""
    cases = collect_cases()
    if not cases:
        sys.exit("no recordings in diagnostics/wake_real")
    rows = run_cases(cases, make_transcriber())
    stamp = time.strftime("%Y%m%d_%H%M%S")
    write_csv(rows, stamp)
    earlier = sorted(RESULTS.glob("wake_*.csv"))[:-1]
    summary = summarize(rows, earlier[-1] if earlier else None)
    (RESULTS / f"wake_{stamp}.summary.json").write_text(json.dumps(summary, indent=1))
    nm = [r for r in rows if not r["must_wake"]]
    wk = [r for r in rows if r["must_wake"]]
    print(
        f"wake regression: {summary['ok_pct']}% ({summary['false_wakes']} false wakes of {len(nm)} name sentences, {summary['wake_misses']} misses of {len(wk)} wake recordings)"
    )
    for r in rows:
        if not r["ok"]:
            print(
                "  FAIL",
                r["id"],
                "must wake" if r["must_wake"] else "must NOT wake",
                "|",
                r["heard"][:70],
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
