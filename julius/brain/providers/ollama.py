"""Ollama's native API (POST {base_url}/api/chat, newline-delimited JSON when streaming).

Ollama has no tool_choice, so a forced tool becomes an instruction in the system prompt (Capabilities.forced_tool_choice
is False). Tool results are matched to calls by name, the way Ollama's chat template expects."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from julius.brain.providers import base
from julius.brain.providers.base import Capabilities, Done, TextDelta, ToolCall, Usage

DEFAULT_CONTEXT = 16_384  # Ollama's own default is far smaller than Julius's system prompt and tool list (about 13k tokens)


def to_messages(request: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system = base.system_text(request.get("system")) + base.forced_tool_note(
        request.get("tool_choice")
    )
    if system:
        out.append({"role": "system", "content": system})
    names: dict[str, str] = {}  # tool_use id -> tool name
    for m in request.get("messages", []):
        parts = base.blocks(m.get("content"))
        if m["role"] == "assistant":
            calls = []
            for b in parts:
                if b.get("type") == "tool_use":
                    names[b["id"]] = b["name"]
                    calls.append(
                        {"function": {"name": b["name"], "arguments": b.get("input") or {}}}
                    )
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": "".join(b.get("text", "") for b in parts if b.get("type") == "text"),
            }
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        text, images = [], []
        for b in parts:
            if b.get("type") == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "content": base.tool_result_text(b.get("content")),
                        "tool_name": names.get(b.get("tool_use_id", ""), ""),
                    }
                )
            elif b.get("type") == "text":
                text.append(b["text"])
            elif b.get("type") == "image" and b.get("source", {}).get("type") == "base64":
                images.append(b["source"]["data"])
        if text or images:
            msg = {"role": "user", "content": "\n\n".join(text)}
            if images:
                msg["images"] = images
            out.append(msg)
    return out


def body(
    request: dict[str, Any], model: str, stream: bool, options: dict[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": model,
        "messages": to_messages(request),
        "stream": stream,
        "options": {
            "num_predict": base.capped(request.get("max_tokens"), options.get("max_output_tokens")),
            "num_ctx": DEFAULT_CONTEXT,
            **options.get("options", {}),
        },
    }
    if (request.get("tool_choice") or {}).get("type") != "none":
        tools = base.function_tools(request.get("tools"))
        if tools:
            out["tools"] = tools
    for key in (
        "keep_alive",
        "think",
    ):  # max_output_tokens and deadline_s are Julius's limits, not Ollama options
        if key in options:
            out[key] = options[key]
    return out


def _bad_output(text: str) -> bool:
    """Ollama's 500 for model output it could not turn into a tool call (the server itself is fine)."""
    low = (text or "").lower()
    return "tool" in low or "pars" in low


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout: float = 120.0,
        capabilities: Capabilities | None = None,
        transport: Any = None,
        options: dict[str, Any] | None = None,
        stats: dict[str, int] | None = None,
    ) -> None:
        self.model, self.base_url, self.timeout = model, base_url.rstrip("/"), timeout
        self.capabilities = capabilities or Capabilities(forced_tool_choice=False)
        self.transport = transport
        self.options = dict(options or {})
        self.stats = stats if stats is not None else {}

    def events(self, request: dict[str, Any], stream: bool = False) -> Iterator[base.Event]:
        import httpx

        # always streamed, even for a one-shot call, so the deadline is checked while the model writes
        payload = body(request, self.model, True, self.options)
        deadline = base.Deadline(self.options.get("deadline_s"))
        read = min(self.timeout, deadline.seconds) if deadline.seconds else self.timeout
        try:
            with (
                httpx.Client(
                    timeout=httpx.Timeout(read, connect=10.0), transport=self.transport
                ) as client,
                client.stream("POST", f"{self.base_url}/api/chat", json=payload) as r,
            ):
                if r.status_code >= 400:
                    r.read()
                    if r.status_code in (401, 403):
                        raise base.ProviderAuthError(f"HTTP {r.status_code}")
                    if r.status_code == 404:
                        raise base.ProviderError(
                            f"model {self.model!r} not found: ollama pull {self.model}"
                        )
                    if r.status_code == 500 and _bad_output(r.text):
                        raise base.ProviderBadOutput(f"HTTP 500: {r.text[:160]}")
                    if r.status_code >= 500 or r.status_code == 429:
                        raise base.ProviderUnavailable(f"HTTP {r.status_code}")
                    raise base.ProviderError(f"HTTP {r.status_code}: {r.text[:200]}")
                yield from self._from_lines(r.iter_lines(), deadline)
        except httpx.TimeoutException as e:
            raise base.ProviderTimeout(f"{self.base_url}: no reply within {read:.0f} s") from e
        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            raise base.ProviderUnavailable(f"{self.base_url}: {type(e).__name__}") from e

    def _from_lines(
        self, lines: Iterator[str], deadline: base.Deadline | None = None
    ) -> Iterator[base.Event]:
        calls, done = [], {}
        for line in lines:
            if deadline is not None:
                deadline.check()
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
            except ValueError:
                continue
            if chunk.get("error"):
                raise base.ProviderBadOutput(str(chunk["error"])[:200])
            msg = chunk.get("message") or {}
            if msg.get("content"):
                yield TextDelta(msg["content"])
            for c in msg.get("tool_calls") or []:
                fn = c.get("function") or {}
                calls.append(
                    ToolCall(
                        c.get("id") or f"call_{len(calls) + 1}",
                        fn.get("name", ""),
                        base.parse_arguments(fn.get("arguments"), self.stats),
                    )
                )
            if chunk.get("done"):
                done = chunk
                break
        yield from calls
        yield Done(
            base.stop_reason(done.get("done_reason"), bool(calls)),
            Usage(
                input_tokens=int(done.get("prompt_eval_count") or 0),
                output_tokens=int(done.get("eval_count") or 0),
            ),
        )
