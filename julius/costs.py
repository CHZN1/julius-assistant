"""Per-turn API cost by tier, a running daily total and a hard daily spend cap.

Rates are USD per million tokens from the Anthropic pricing page (checked 2026-10-24):
Haiku 5.5 $0.10 in / $0.125 5m cache write / $0.01 cache read / $0.50 out for prompts up to 100K
tokens ($0.50 / $0.625 / $0.05 / $2.50 above), Sonnet 5.5 $2 / $2.50 / $0.10 / $10.
Thinking tokens are billed as output and are already included in usage.output_tokens.
Another provider's usage (bring your own model) carries its own per-million rates from [models.<tier>] price_in and
price_out, and a local model costs $0. Every ledger row records the provider and model that answered.
"""

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

RATES = {
    "haiku": {
        "model": "claude-haiku-5-5",
        "in": 0.10,
        "write": 0.125,
        "read": 0.01,
        "out": 0.50,
        "long_over": 100_000,
        "long": {"in": 0.50, "write": 0.625, "read": 0.05, "out": 2.50},
    },
    "sonnet": {"model": "claude-sonnet-5-5", "in": 2.00, "write": 2.50, "read": 0.10, "out": 10.00},
}


def _n(usage: SimpleNamespace | dict[str, int], name: str) -> int:
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
    return int(value or 0)


def _fields(usage: SimpleNamespace | dict[str, int]) -> dict[str, object]:
    return (
        dict(usage)
        if isinstance(usage, dict)
        else dict(vars(usage))
        if hasattr(usage, "__dict__")
        else {}
    )


def cost_of(tier: str, usage: SimpleNamespace | dict[str, int]) -> float:
    """USD for one response's usage (input, output, cache-write and cache-read tokens)."""
    provider = (
        getattr(usage, "provider", None) if not isinstance(usage, dict) else usage.get("provider")
    )
    if provider and provider != "anthropic":
        rates = (
            getattr(usage, "rates", None) if not isinstance(usage, dict) else usage.get("rates")
        ) or {}
        fresh = _n(usage, "input_tokens") + _n(usage, "cache_read_input_tokens")
        return (
            fresh * float(rates.get("in", 0.0))
            + _n(usage, "output_tokens") * float(rates.get("out", 0.0))
        ) / 1_000_000
    card = RATES[tier]
    fresh, out = _n(usage, "input_tokens"), _n(usage, "output_tokens")
    write, read = _n(usage, "cache_creation_input_tokens"), _n(usage, "cache_read_input_tokens")
    if card.get("long") and fresh + write + read > card["long_over"]:
        card = {**card, **card["long"]}
    return (
        fresh * card["in"] + write * card["write"] + read * card["read"] + out * card["out"]
    ) / 1_000_000


SEPARATE_TIERS = {
    "code"
}  # coding-agent spend has its own daily budget and never counts toward the voice cap


class CostTracker:
    def __init__(
        self,
        path: Path,
        cap_usd: float,
        clock: Callable = time.time,
        offline_multiple: float = 2.0,
    ) -> None:
        self.path, self.cap_usd, self._clock = Path(path), float(cap_usd), clock
        self.offline_multiple = float(offline_multiple)
        self._lock = threading.Lock()
        self.turn = 0
        self._day, self._total, self._by_tier = None, 0.0, {}
        self._turn_costs = {}
        self._load_today()

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")

    def _load_today(self) -> None:
        self._day, self._total, self._by_tier = self._today(), 0.0, {}
        if self.path.is_file():
            for line in self.path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("date") == self._day:
                    if row["tier"] not in SEPARATE_TIERS:
                        self._total += row["usd"]
                    self._by_tier[row["tier"]] = self._by_tier.get(row["tier"], 0.0) + row["usd"]

    def _roll(self) -> None:
        if self._today() != self._day:
            self._load_today()

    def begin_turn(self) -> int:
        with self._lock:
            self.turn += 1
            self._turn_costs = {}
            return self.turn

    def record(
        self,
        tier: str,
        usage: SimpleNamespace,
        note: str = "",
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, str | float | int]:
        """Log one API response with the provider and model that answered (given, or tagged on the usage). Returns the log row."""
        provider = provider or getattr(usage, "provider", None) or "anthropic"
        model = model or getattr(usage, "model", None) or RATES[tier]["model"]
        if provider != "anthropic" and getattr(usage, "provider", None) != provider:
            usage = SimpleNamespace(**{**_fields(usage), "provider": provider})
        usd = cost_of(tier, usage)
        with self._lock:
            self._roll()
            row = {
                "ts": round(self._clock(), 3),
                "date": self._day,
                "turn": self.turn,
                "tier": tier,
                "provider": provider,
                "model": model,
                "input": _n(usage, "input_tokens"),
                "output": _n(usage, "output_tokens"),
                "cache_write": _n(usage, "cache_creation_input_tokens"),
                "cache_read": _n(usage, "cache_read_input_tokens"),
                "usd": round(usd, 8),
                "note": note,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            self._total += usd
            self._by_tier[tier] = self._by_tier.get(tier, 0.0) + usd
            self._turn_costs[tier] = self._turn_costs.get(tier, 0.0) + usd
            return row

    def record_usd(
        self, tier: str, usd: float, note: str = "", provider: str = "anthropic"
    ) -> dict[str, str | float | int]:
        """Log a cost already priced elsewhere (the coding agent, `claude -p`, reports its own dollars)."""
        with self._lock:
            self._roll()
            row = {
                "ts": round(self._clock(), 3),
                "date": self._day,
                "turn": self.turn,
                "tier": tier,
                "provider": provider,
                "model": note,
                "input": 0,
                "output": 0,
                "cache_write": 0,
                "cache_read": 0,
                "usd": round(float(usd), 8),
                "note": note,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            if tier not in SEPARATE_TIERS:
                self._total += float(usd)
            self._by_tier[tier] = self._by_tier.get(tier, 0.0) + float(usd)
            return row

    def code_today(self) -> int | float:
        with self._lock:
            self._roll()
            return sum(v for k, v in self._by_tier.items() if k in SEPARATE_TIERS)

    def today_total(self) -> float:
        with self._lock:
            self._roll()
            return self._total

    def today_by_tier(self) -> dict[str, float]:
        with self._lock:
            self._roll()
            return dict(self._by_tier)

    def turn_costs(self) -> dict[str, float]:
        with self._lock:
            return dict(self._turn_costs)

    def cap_reached(self) -> bool:
        """At the cap: Haiku only (no planner)."""
        return self.today_total() >= self.cap_usd

    def offline_required(self) -> bool:
        """At offline_multiple times the cap: stop calling the API altogether and use the offline model."""
        return self.today_total() >= self.cap_usd * self.offline_multiple

    def status_line(self) -> str:
        costs = self.turn_costs()
        parts = " ".join(f"{tier} ${usd:.5f}" for tier, usd in costs.items()) or "no API calls"
        state = (
            " | OFFLINE (2x cap)"
            if self.offline_required()
            else " | cap reached: Haiku only"
            if self.cap_reached()
            else ""
        )
        return (
            f"  [cost: {parts} | turn ${sum(costs.values()):.5f} | "
            f"today ${self.today_total():.4f} of ${self.cap_usd:.2f}{state}]"
        )
