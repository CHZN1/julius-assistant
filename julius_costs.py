"""Per-turn API cost by tier, a running daily total and a hard daily spend cap.

Rates are USD per million tokens from the Anthropic pricing page (checked 2026-10-24):
Haiku 5.5 $0.10 in / $0.125 5m cache write / $0.01 cache read / $0.50 out for prompts up to 100K
tokens ($0.50 / $0.625 / $0.05 / $2.50 above), Sonnet 5.5 $2 / $2.50 / $0.10 / $10.
Thinking tokens are billed as output and are already included in usage.output_tokens.
"""
import json, threading, time
from datetime import datetime
from pathlib import Path

RATES = {
    "haiku": {"model": "claude-haiku-5-5", "in": 0.10, "write": 0.125, "read": 0.01, "out": 0.50,
              "long_over": 100_000, "long": {"in": 0.50, "write": 0.625, "read": 0.05, "out": 2.50}},
    "sonnet": {"model": "claude-sonnet-5-5", "in": 2.00, "write": 2.50, "read": 0.10, "out": 10.00},
}


def _n(usage, name):
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
    return int(value or 0)


def cost_of(tier, usage):
    """USD for one response's usage (input, output, cache-write and cache-read tokens)."""
    card = RATES[tier]
    fresh, out = _n(usage, "input_tokens"), _n(usage, "output_tokens")
    write, read = _n(usage, "cache_creation_input_tokens"), _n(usage, "cache_read_input_tokens")
    if card.get("long") and fresh + write + read > card["long_over"]:
        card = {**card, **card["long"]}
    return (fresh * card["in"] + write * card["write"] + read * card["read"] + out * card["out"]) / 1_000_000


class CostTracker:
    def __init__(self, path, cap_usd, clock=time.time, offline_multiple=2.0):
        self.path, self.cap_usd, self._clock = Path(path), float(cap_usd), clock
        self.offline_multiple = float(offline_multiple)
        self._lock = threading.Lock()
        self.turn = 0
        self._day, self._total, self._by_tier = None, 0.0, {}
        self._turn_costs = {}
        self._load_today()

    def _today(self):
        return datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")

    def _load_today(self):
        self._day, self._total, self._by_tier = self._today(), 0.0, {}
        if self.path.is_file():
            for line in self.path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("date") == self._day:
                    self._total += row["usd"]
                    self._by_tier[row["tier"]] = self._by_tier.get(row["tier"], 0.0) + row["usd"]

    def _roll(self):
        if self._today() != self._day:
            self._load_today()

    def begin_turn(self):
        with self._lock:
            self.turn += 1
            self._turn_costs = {}
            return self.turn

    def record(self, tier, usage, note=""):
        """Log one API response. Returns the log row."""
        usd = cost_of(tier, usage)
        with self._lock:
            self._roll()
            row = {"ts": round(self._clock(), 3), "date": self._day, "turn": self.turn, "tier": tier,
                   "model": RATES[tier]["model"], "input": _n(usage, "input_tokens"), "output": _n(usage, "output_tokens"),
                   "cache_write": _n(usage, "cache_creation_input_tokens"), "cache_read": _n(usage, "cache_read_input_tokens"),
                   "usd": round(usd, 8), "note": note}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            self._total += usd
            self._by_tier[tier] = self._by_tier.get(tier, 0.0) + usd
            self._turn_costs[tier] = self._turn_costs.get(tier, 0.0) + usd
            return row

    def today_total(self):
        with self._lock:
            self._roll()
            return self._total

    def today_by_tier(self):
        with self._lock:
            self._roll()
            return dict(self._by_tier)

    def turn_costs(self):
        with self._lock:
            return dict(self._turn_costs)

    def cap_reached(self):
        """At the cap: Haiku only (no planner)."""
        return self.today_total() >= self.cap_usd

    def offline_required(self):
        """At offline_multiple times the cap: stop calling the API altogether and use the offline model."""
        return self.today_total() >= self.cap_usd * self.offline_multiple

    def status_line(self):
        costs = self.turn_costs()
        parts = " ".join(f"{tier} ${usd:.5f}" for tier, usd in costs.items()) or "no API calls"
        state = " | OFFLINE (2x cap)" if self.offline_required() else " | cap reached: Haiku only" if self.cap_reached() else ""
        return (f"  [cost: {parts} | turn ${sum(costs.values()):.5f} | "
                f"today ${self.today_total():.4f} of ${self.cap_usd:.2f}{state}]")
