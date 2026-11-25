"""The provider interface: one request shape in, normalized events out.

Request: the Anthropic Messages API keyword arguments Julius already builds everywhere (model, system, messages, tools,
tool_choice, max_tokens). They are the canonical format because every call site already speaks it; an adapter
translates them into its own wire format and drops what it cannot use (cache_control, thinking, output_config, strict).

Events, in order:
  TextDelta(text)                 zero or more, as the reply streams
  ToolCall(id, name, input)       zero or more, each complete (input is a dict)
  Done(stop_reason, usage)        exactly one, last; stop_reason is "end_turn", "tool_use" or "max_tokens"

Errors: ProviderAuthError (bad or missing key: spoken once, then the brain is disabled until restart) and
ProviderUnavailable (connection, timeout, rate limit, server error: the front end falls back to the offline model).
Anything else is a ProviderError and is a bug in the request."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Capabilities:
    tool_calling: bool = True
    forced_tool_choice: bool = True  # can be made to call a named tool ("any" / {"type": "tool"})
    vision: bool = False
    prompt_caching: bool = False
    context_tokens: int = 32_000


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Done:
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)


Event = TextDelta | ToolCall | Done


class ProviderError(Exception):
    pass


class ProviderAuthError(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class ProviderTimeout(ProviderUnavailable):
    """The reply ran past its deadline (a slow server, or a local model stuck repeating itself)."""


class ProviderBadOutput(ProviderError):
    """The server answered but the model's output was unusable (a tool call it could not parse). Fails only that turn:
    the server is up, so it never counts toward going offline."""


def capped(requested: Any, cap: int | None) -> int:
    """max_tokens for the request, never above the tier's output cap."""
    n = int(requested or 1024)
    return min(n, int(cap)) if cap else n


class Deadline:
    """A wall-clock limit for one reply, checked as each chunk arrives (the HTTP read timeout covers silent gaps)."""

    def __init__(self, seconds: float | None, clock: Any = None) -> None:
        import time

        self.seconds, self._clock = seconds, clock or time.monotonic
        self.start = self._clock()

    def check(self) -> None:
        if self.seconds and self._clock() - self.start > self.seconds:
            raise ProviderTimeout(f"no complete reply within {self.seconds:.0f} s")


class Provider(Protocol):
    name: str
    capabilities: Capabilities

    def events(self, request: dict[str, Any], stream: bool = False) -> Iterator[Event]: ...


# ---------- shared translation helpers ----------
def system_text(system: Any) -> str:
    """The system prompt as one string (Julius sends either a string or a list of text blocks)."""
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system if b.get("type", "text") == "text")


def blocks(content: Any) -> list[dict[str, Any]]:
    """Message content as a list of blocks (a plain string becomes one text block)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    return list(content or [])


def tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in blocks(content) if b.get("type") == "text")


def function_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Messages API tools -> OpenAI/Ollama function tools (cache_control and strict dropped)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for t in tools or []
        if "name" in t
        and "input_schema"
        in t  # server tools (web search and the like) have no schema to translate
    ]


def forced_tool_note(tool_choice: Any) -> str:
    """For a provider that cannot force a tool call: the instruction that stands in for tool_choice."""
    kind = (tool_choice or {}).get("type")
    if kind == "tool":
        return f"\n\nAnswer this turn by calling the {tool_choice['name']} tool."
    if kind == "any":
        return "\n\nAnswer this turn by calling one of your tools."
    return ""


def parse_arguments(raw: Any, stats: dict[str, int] | None = None) -> dict[str, Any]:
    """Tool-call arguments as a dict. Invalid JSON counts against the provider (stats["invalid_json"]) and yields {}."""
    if stats is not None:
        stats["tool_calls"] = stats.get("tool_calls", 0) + 1
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        if isinstance(value, dict):
            return value
    except (TypeError, ValueError):
        pass
    if stats is not None:
        stats["invalid_json"] = stats.get("invalid_json", 0) + 1
    return {}


STOP_REASONS = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
}


def stop_reason(raw: str | None, had_tools: bool) -> str:
    if had_tools:
        return "tool_use"
    return STOP_REASONS.get(raw or "stop", "end_turn")


def collect(events: Iterator[Event]) -> tuple[str, list[ToolCall], Done]:
    """Drain an event stream into (text, tool calls, done)."""
    text, calls, done = [], [], Done()
    for e in events:
        if isinstance(e, TextDelta):
            text.append(e.text)
        elif isinstance(e, ToolCall):
            calls.append(e)
        elif isinstance(e, Done):
            done = e
    return "".join(text), calls, done
