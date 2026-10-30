"""Shared helpers for the eval suites: dated result CSVs, previous-run lookup and regression flags."""
import csv, json, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

def write_results(suite, rows, summary):
    """Write results/<suite>_<timestamp>.csv plus a .summary.json; returns (csv_path, previous_csv_path_or_None)."""
    RESULTS.mkdir(exist_ok=True)
    previous = latest(suite)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = RESULTS / f"{suite}_{stamp}.csv"
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    path.with_suffix(".summary.json").write_text(json.dumps(summary, indent=1))
    return path, previous

def latest(suite):
    files = sorted(RESULTS.glob(f"{suite}_*.csv"))
    return files[-1] if files else None

def load_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))

def regressions(rows, previous_path, key="id", ok="ok"):
    """Cases that passed in the previous run and fail now (and new failures); also cases fixed since."""
    if previous_path is None:
        return [], [], "no previous run"
    old = {r[key]: r[ok] == "True" for r in load_rows(previous_path)}
    now = {r[key]: str(r[ok]) == "True" for r in rows}
    broke = sorted(k for k, v in now.items() if not v and old.get(k))
    fixed = sorted(k for k, v in now.items() if v and old.get(k) is False)
    return broke, fixed, previous_path.name
