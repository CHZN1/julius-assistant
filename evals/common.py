"""Shared helpers for the eval suites: dated result CSVs, previous-run lookup and regression flags."""

import csv
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


SUITE_TIER = {  # the model tier each suite measures (bring your own model); suites without a model call are "-"
    "routing": "front_end",
    "partial_routing": "-",
    "routing_shake": "front_end",
    "planner": "planner",
    "research": "research",
    "vision": "vision",
    "code": "coding",
    "file": "-",
    "music": "-",
    "audio": "front_end",
    "livedata": "front_end",
    "memory": "front_end",
}


def provider_of(suite):
    """'provider:model at url' for the tier this suite measures (from julius.toml and JULIUS_TIER_* overrides), or '-'."""
    tier = SUITE_TIER.get(suite, "front_end")
    if tier == "-":
        return "-"
    try:
        import julius.config as cfg
        from julius.brain import providers

        return providers.resolve(tier, cfg.load_config()).describe()
    except Exception as e:  # a broken config is reported in the column, not hidden
        return f"? ({e})"


def write_results(suite, rows, summary):
    """Write results/<suite>_<timestamp>.csv plus a .summary.json; returns (csv_path, previous_csv_path_or_None).
    Every row and the summary carry the provider and model of the tier the suite measures."""
    RESULTS.mkdir(exist_ok=True)
    provider = provider_of(suite)
    rows = [{**r, "provider": provider} for r in rows]
    summary.setdefault("provider", provider)
    previous = latest(suite)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = RESULTS / f"{suite}_{stamp}.csv"
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
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
