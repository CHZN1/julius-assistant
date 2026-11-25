"""A Messages-API-shaped client over any provider, so every call site that does `client.messages.create(...)` or
`client.messages.stream(...)` works unchanged whatever the tier's provider is.

Responses look like the SDK's: `.content` (blocks with .type "text"/"tool_use"), `.stop_reason`, `.model` and `.usage`
(input/output/cache token counts, tagged with provider, model and the tier's per-million prices for the cost ledger)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

from julius.brain.providers.base import Done, Event, TextDelta, ToolCall


def _usage(
    done: Done | None, provider: str, model: str, rates: dict[str, float]
) -> SimpleNamespace:
    u = done.usage if done else None
    return SimpleNamespace(
        input_tokens=getattr(u, "input_tokens", 0),
        output_tokens=getattr(u, "output_tokens", 0),
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0),
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0),
        provider=provider,
        model=model,
        rates=dict(rates),
    )


def message(
    text: str,
    calls: list[ToolCall],
    done: Done | None,
    provider: str,
    model: str,
    rates: dict[str, float],
) -> SimpleNamespace:
    content = [SimpleNamespace(type="text", text=text)] if text else []
    content += [
        SimpleNamespace(type="tool_use", id=c.id, name=c.name, input=c.input) for c in calls
    ]
    return SimpleNamespace(
        id="msg_julius",
        role="assistant",
        type="message",
        model=model,
        content=content,
        stop_reason=(done.stop_reason if done else "end_turn"),
        usage=_usage(done, provider, model, rates),
    )


class _Stream:
    """Context manager with the SDK stream's surface: text_stream, get_final_message(), current_message_snapshot."""

    def __init__(
        self, events: Iterator[Event], provider: str, model: str, rates: dict[str, float]
    ) -> None:
        self._events, self._provider, self._model, self._rates = events, provider, model, rates
        self._text: list[str] = []
        self._calls: list[ToolCall] = []
        self._done: Done | None = None
        self._drained = False

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *exc: Any) -> None:
        close = getattr(self._events, "close", None)
        if close:
            close()

    def _pull(self) -> Iterator[str]:
        for e in self._events:
            if isinstance(e, TextDelta):
                self._text.append(e.text)
                yield e.text
            elif isinstance(e, ToolCall):
                self._calls.append(e)
            elif isinstance(e, Done):
                self._done = e
        self._drained = True

    @property
    def text_stream(self) -> Iterator[str]:
        return self._pull()

    def get_final_message(self) -> SimpleNamespace:
        if not self._drained:
            for _ in self._pull():
                pass
        return self.current_message_snapshot

    @property
    def current_message_snapshot(self) -> SimpleNamespace:
        return message(
            "".join(self._text), self._calls, self._done, self._provider, self._model, self._rates
        )


class _Messages:
    def __init__(self, owner: MessagesClient) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> SimpleNamespace:
        o = self._owner
        text, calls, done = [], [], None
        for e in o.provider.events(kwargs, stream=False):
            if isinstance(e, TextDelta):
                text.append(e.text)
            elif isinstance(e, ToolCall):
                calls.append(e)
            elif isinstance(e, Done):
                done = e
        return message("".join(text), calls, done, o.provider.name, o.provider.model, o.rates)

    def stream(self, **kwargs: Any) -> _Stream:
        o = self._owner
        return _Stream(
            o.provider.events(kwargs, stream=True), o.provider.name, o.provider.model, o.rates
        )


class MessagesClient:
    def __init__(self, provider: Any, rates: dict[str, float] | None = None) -> None:
        self.provider = provider
        self.rates = dict(rates or {})
        self.messages = _Messages(self)
