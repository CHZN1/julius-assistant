"""Anthropic: the Messages API through the official SDK.

The request format is already the Messages API, so nothing is translated: `client()` hands call sites the SDK client
itself (wrapped only to tag each response's usage with provider and model for the cost ledger), and `events()` gives the
same normalized events as every other adapter."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from julius.brain.providers import base
from julius.brain.providers.base import Capabilities, Done, TextDelta, ToolCall, Usage

CAPABILITIES = Capabilities(
    tool_calling=True,
    forced_tool_choice=True,
    vision=True,
    prompt_caching=True,
    context_tokens=200_000,
)


def _usage(u: Any) -> Usage:
    return Usage(
        input_tokens=int(getattr(u, "input_tokens", 0) or 0),
        output_tokens=int(getattr(u, "output_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
    )


def tag(usage: Any, provider: str, model: str) -> Any:
    """Mark a usage object with where it came from (the ledger's provider and model columns)."""
    with contextlib.suppress(AttributeError, TypeError, ValueError):
        usage.provider, usage.model = provider, model
    return usage


class _Messages:
    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk

    def create(self, **kwargs: Any) -> Any:
        r = self._sdk.messages.create(**kwargs)
        tag(
            getattr(r, "usage", None),
            "anthropic",
            getattr(r, "model", None) or kwargs.get("model", ""),
        )
        return r

    def stream(self, **kwargs: Any) -> Any:
        return self._sdk.messages.stream(**kwargs)

    def __getattr__(
        self, name: str
    ) -> Any:  # count_tokens, batches and the rest pass straight through
        return getattr(self._sdk.messages, name)


class TaggingClient:
    """The SDK client with `messages.create` usage tagged; everything else is the SDK unchanged."""

    def __init__(self, sdk: Any) -> None:
        self._sdk = sdk
        self.messages = _Messages(sdk)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sdk, name)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, client: Any) -> None:
        self.model, self.capabilities = model, CAPABILITIES
        self._client = client

    def client(self) -> Any:
        return self._client

    def events(self, request: dict[str, Any], stream: bool = False) -> Iterator[base.Event]:
        import anthropic

        kwargs = dict(request, model=request.get("model") or self.model)
        try:
            if stream:
                with self._client.messages.stream(**kwargs) as s:
                    for text in s.text_stream:
                        yield TextDelta(text)
                    final = s.get_final_message()
            else:
                final = self._client.messages.create(**kwargs)
                for b in final.content:
                    if getattr(b, "type", "") == "text" and b.text:
                        yield TextDelta(b.text)
        except anthropic.AuthenticationError as e:
            raise base.ProviderAuthError(str(e)) from e
        except (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ) as e:
            raise base.ProviderUnavailable(type(e).__name__) from e
        calls = [b for b in final.content if getattr(b, "type", "") == "tool_use"]
        for b in calls:
            yield ToolCall(b.id, b.name, dict(b.input))
        yield Done(
            base.stop_reason(None, bool(calls)) if calls else (final.stop_reason or "end_turn"),
            _usage(final.usage),
        )
