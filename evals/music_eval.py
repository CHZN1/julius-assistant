"""Music analysis eval (free, local): BPM and key accuracy on labelled audio.

Three sets:
  synthetic  generated here (drums at a known tempo under chords in a known key), always run
  real       Creative Commons / public-domain tracks listed in ~/datasets/julius_music_eval/manifest.json
             [{"file": "x.mp3", "bpm": 120, "key": "A minor", "source": "url", "license": "CC BY 4.0"}], files in the same folder; skipped if absent
  local      your own labelled files, ~/datasets/julius_music_eval/local/manifest.json (same format, absolute or relative paths).
             Printed only: never written to evals/results and never committed.

Scores: BPM within 2% ("exact"), BPM within 2% of the true tempo or half or double ("octave"), key exactly right, key "close" (the same notes: relative
major/minor, or a perfect fifth away).

Usage: python evals/music_eval.py [--synthetic-only]"""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
import common

from julius.music import analysis, testsignals

DATA = Path.home() / "datasets" / "julius_music_eval"
KEYS = [
    "C major",
    "A minor",
    "G major",
    "E minor",
    "D major",
    "B minor",
    "F major",
    "D minor",
    "Bb major",
    "G minor",
    "A major",
    "F# minor",
    "Eb major",
    "C minor",
    "E major",
    "C# minor",
]
TEMPOS = [62, 70, 78, 85, 92, 100, 108, 116, 124, 128, 135, 140, 150, 160, 170, 180]
ORDER = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}


def pc(key: str) -> tuple[int, str]:
    name, mode = key.split()
    return ORDER[name], mode


def close_key(found: str, truth: str) -> bool:
    (a, am), (b, bm) = pc(found), pc(truth)
    if found == truth or (a, am) == (b, bm):
        return True
    if am != bm:  # relative major/minor share their notes
        major, minor = (a, b) if am == "major" else (b, a)
        return (major - minor) % 12 == 3
    return (a - b) % 12 in (5, 7)  # a fifth apart


def score(row: dict) -> dict:
    t, f = row["bpm"], row["found_bpm"]
    row["bpm_exact"] = abs(f - t) / t <= 0.02
    row["bpm_octave"] = any(abs(f - t * r) / (t * r) <= 0.02 for r in (0.5, 1, 2))
    row["key_exact"] = row["found_key"] == row["key"]
    row["key_close"] = close_key(row["found_key"], row["key"]) if row["found_key"] else False
    return row


def run_one(path: Path, bpm: float, key: str, name: str, group: str) -> dict:
    t0 = time.time()
    try:
        a = analysis.analyze(path)
    except Exception as exc:
        return score(
            {
                "set": group,
                "name": name,
                "bpm": bpm,
                "key": key,
                "found_bpm": 0.0,
                "found_key": "",
                "bpm_conf": 0,
                "key_conf": 0,
                "seconds": round(time.time() - t0, 1),
                "error": str(exc)[:80],
            }
        )
    return score(
        {
            "set": group,
            "name": name,
            "bpm": bpm,
            "key": key,
            "found_bpm": a.bpm,
            "found_key": a.key,
            "bpm_conf": a.bpm_confidence,
            "key_conf": a.key_confidence,
            "seconds": round(time.time() - t0, 1),
            "error": "",
        }
    )


def synthetic() -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, bpm in enumerate(TEMPOS):
            key = KEYS[i]
            p = Path(tmp) / f"s{i}.wav"
            testsignals.write(p, bpm, key, 30.0, seed=100 + i)
            rows.append(run_one(p, bpm, key, f"{bpm} BPM {key}", "synthetic"))
    return rows


def from_manifest(manifest: Path, group: str) -> list[dict]:
    if not manifest.exists():
        return []
    rows = []
    for e in json.loads(manifest.read_text()):
        p = Path(e["file"]).expanduser()
        p = p if p.is_absolute() else manifest.parent / p
        rows.append(run_one(p, float(e["bpm"]), e["key"], e.get("name") or p.name, group))
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    pct = lambda k: round(100 * sum(1 for r in rows if r[k]) / n)  # noqa: E731
    return {
        "n": n,
        "bpm_exact_pct": pct("bpm_exact"),
        "bpm_octave_pct": pct("bpm_octave"),
        "key_exact_pct": pct("key_exact"),
        "key_close_pct": pct("key_close"),
        "mean_seconds": round(sum(r["seconds"] for r in rows) / n, 1),
    }


def show(group: str, rows: list[dict]) -> None:
    print(f"\n== {group} ==")
    for r in rows:
        mark = lambda ok: "ok" if ok else "--"  # noqa: E731
        print(
            f"  {r['name'][:34]:34} true {r['bpm']:>5g} {r['key']:<9} found {r['found_bpm']:>6g} {r['found_key']:<9} bpm {mark(r['bpm_exact'])}/{mark(r['bpm_octave'])} key {mark(r['key_exact'])}/{mark(r['key_close'])} conf {r['bpm_conf']:.2f}/{r['key_conf']:.2f} {r['error']}"
        )
    print("  ", summarize(rows))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic-only", action="store_true")
    args = ap.parse_args()
    groups = {"synthetic": synthetic()}
    if not args.synthetic_only:
        groups["real"] = from_manifest(DATA / "manifest.json", "real")
        groups["local"] = from_manifest(DATA / "local" / "manifest.json", "local")
    summary = {}
    for g, rows in groups.items():
        if rows:
            show(g, rows)
            summary[g] = summarize(rows)
        else:
            print(f"\n== {g} == (none)")
    public = [r for g in ("synthetic", "real") for r in groups.get(g, [])]
    path, _ = common.write_results(
        "music_analysis",
        public,
        summary.get("synthetic") and {k: v for k, v in summary.items() if k != "local"},
    )
    print("\nwrote", path.name, "(your own local files are not written anywhere)")


if __name__ == "__main__":
    main()
