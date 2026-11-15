"""Real-music eval from the user's own files, labelled by the BPM written in the file name (_BPM140, _bpm94, ...). Free, local, read-only.

The label list, the cached audio and the results live in ~/datasets/julius_music_eval/local/ and are never committed or written under evals/results.

  python evals/music_filename_eval.py prepare [--n 200]   pick a spread sample from the sample index and cache its audio
  python evals/music_filename_eval.py run [--split dev|holdout|all] [--tag name]   tempo accuracy on the sample
  python evals/music_filename_eval.py keys                 key confidence on drum-named vs melody-named files (tunes the "no clear key" threshold)

A file name is a label made by a person: it can be wrong, so a "miss" is a disagreement, not proof the detector is wrong."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from julius.music import analysis, library

LOCAL = Path.home() / "datasets" / "julius_music_eval" / "local"
CACHE = LOCAL / "cache"
SAMPLE = LOCAL / "filename_sample.json"
SEED = 20261114

DRUM_RE = re.compile(
    r"\b(drums?|perc\w*|hi.?hats?|hats?|kicks?|snares?|claps?)\b"
)  # names that say drums only
MELODY_WORDS = (
    "melod",
    "melody",
    "keys",
    "piano",
    "guitar",
    "pluck",
    "synth",
    "pad",
    "chord",
    "lead",
    "bell",
    "strings",
)


def relation(detected: float, label: float) -> str:
    """How the detected tempo relates to the label: exact (2%), half/double (3%), 3:2, 4:3, other."""
    r = detected / label
    if abs(r - 1) <= 0.02:
        return "exact"
    for name, ratios in (
        ("half/double", (0.5, 2.0)),
        ("3:2", (1.5, 2 / 3)),
        ("4:3", (4 / 3, 0.75)),
    ):
        if any(abs(r / x - 1) <= 0.03 for x in ratios):
            return name
    return "other"


def _load_one(item: tuple[int, dict]) -> tuple[int, dict | None]:
    i, row = item
    try:
        y, total, seconds = analysis.load(Path(row["real"]))
        if seconds < 3:
            return i, None
        np.save(CACHE / f"{i}.npy", y.astype("float16"))
        return i, {"duration": total}
    except Exception:
        return i, None


def prepare(n: int) -> None:
    LOCAL.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    import sqlite3

    db = sqlite3.connect(Path.home() / ".julius" / "sample_index.sqlite3")
    db.row_factory = sqlite3.Row
    best: dict[tuple, dict] = {}
    for r in db.execute(
        "SELECT path, name, folder, ext, bpm, key FROM samples WHERE bpm IS NOT NULL"
    ):
        real = library.real_path(r["path"])
        if real is None or not real.exists():
            continue
        k = (r["folder"], r["name"].casefold())
        rank = 0 if r["ext"].casefold() == ".wav" else 1
        if k not in best or rank < best[k]["rank"]:
            best[k] = {
                "rank": rank,
                "real": str(real),
                "name": r["name"],
                "folder": r["folder"],
                "bpm": r["bpm"],
                "key": r["key"],
            }
    rng = random.Random(SEED)
    by_folder: dict[str, list[dict]] = {}
    for row in best.values():
        by_folder.setdefault(row["folder"], []).append(row)
    for rows in by_folder.values():
        rng.shuffle(rows)
    folders = sorted(by_folder)
    rng.shuffle(folders)
    picked: list[dict] = []
    while len(picked) < n and any(
        by_folder.values()
    ):  # round-robin over folders: a spread, not a pile from the biggest one
        for f in folders:
            if by_folder[f] and len(picked) < n:
                picked.append(by_folder[f].pop())
    picked.sort(key=lambda r: (r["folder"], r["name"]))
    for j, row in enumerate(picked):
        row["split"] = "dev" if j % 2 == 0 else "holdout"
        row.pop("rank", None)
    with Pool(6) as pool:
        done = dict(pool.map(_load_one, list(enumerate(picked))))
    keep = []
    for i, row in enumerate(picked):
        if done.get(i):
            row["id"] = i
            row["duration"] = done[i]["duration"]
            keep.append(row)
    SAMPLE.write_text(json.dumps(keep, indent=1))
    print(
        f"sample: {len(keep)} files from {len({r['folder'] for r in keep})} folders; dev {sum(r['split'] == 'dev' for r in keep)}, holdout {sum(r['split'] == 'holdout' for r in keep)}"
    )


def _tempo_one(item: tuple[int, int]) -> tuple[int, float, float, list[float]]:
    i, _ = item
    y = np.load(CACHE / f"{i}.npy").astype("float32")
    try:
        bpm, conf, alts = analysis.tempo(y)
    except Exception:
        return i, 0.0, 0.0, []
    return i, bpm, conf, alts


def run(split: str, tag: str) -> dict:
    rows = json.loads(SAMPLE.read_text())
    rows = [r for r in rows if split == "all" or r["split"] == split]
    with Pool(6) as pool:
        out = {i: (b, c, a) for i, b, c, a in pool.map(_tempo_one, [(r["id"], 0) for r in rows])}
    res = []
    for r in rows:
        b, c, a = out[r["id"]]
        res.append(
            {
                **{k: r[k] for k in ("id", "name", "folder", "bpm", "split")},
                "detected": round(b, 1),
                "confidence": c,
                "alts": a,
                "rel": relation(b, r["bpm"]) if b else "none",
            }
        )
    (LOCAL / f"results_{tag}_{split}.json").write_text(json.dumps(res, indent=1))
    return summary(res, tag, split)


def summary(res: list[dict], tag: str, split: str) -> dict:
    c = Counter(r["rel"] for r in res)
    n = len(res)
    print(f"\n[{tag}] {split}: {n} files")
    for k in ("exact", "half/double", "3:2", "4:3", "other", "none"):
        if c[k]:
            print(f"  {k:12s} {c[k]:4d}  {100 * c[k] / n:5.1f}%")
    worst = [r for r in res if r["rel"] not in ("exact",)]
    worst.sort(key=lambda r: -abs(np.log2(r["detected"] / r["bpm"])) if r["detected"] else -9)
    print("  worst 10 (by distance from label):")
    for r in worst[:10]:
        print(
            f"    label {r['bpm']:>4}  detected {r['detected']:>6}  {r['rel']:11s} conf {r['confidence']:.2f}  {r['name'][:60]}"
        )
    return {k: c[k] for k in c} | {"n": n}


def keys() -> None:
    import sqlite3

    db = sqlite3.connect(Path.home() / ".julius" / "sample_index.sqlite3")
    db.row_factory = sqlite3.Row
    rng = random.Random(SEED)
    groups: dict[str, list[dict]] = {"drum": [], "melody": []}
    seen = set()
    for r in db.execute("SELECT path, name, folder, ext FROM samples"):
        low = r["name"].casefold()
        g = (
            "drum"
            if DRUM_RE.search(low)
            else "melody"
            if any(w in low for w in MELODY_WORDS)
            else None
        )
        if g is None or (r["folder"], low) in seen:
            continue
        real = library.real_path(r["path"])
        if real is None or not real.exists():
            continue
        seen.add((r["folder"], low))
        groups[g].append({"real": str(real), "name": r["name"]})
    out = []
    for g, rows in groups.items():
        rng.shuffle(rows)
        for row in rows[:40]:
            out.append({**row, "group": g})
    with Pool(6) as pool:
        feats = pool.map(_key_one, out)
    res = [{**o, **f} for o, f in zip(out, feats, strict=True) if f]
    (LOCAL / "key_features.json").write_text(json.dumps(res, indent=1))
    print(f"key features: {Counter(r['group'] for r in res)}")


def _key_one(row: dict) -> dict | None:
    try:
        y, _, seconds = analysis.load(Path(row["real"]))
        if seconds < 3:
            return None
        return analysis.key_features(y)
    except Exception:
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "run", "keys"])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--split", default="all", choices=["dev", "holdout", "all"])
    ap.add_argument("--tag", default="run")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.n)
    elif a.cmd == "run":
        run(a.split, a.tag)
    else:
        keys()
