"""Any OpenAI-compatible chat endpoint: LM Studio, llama.cpp llama-server, vLLM, OpenRouter, Ollama's /v1 and the like.

POST {base_url}/chat/completions, streamed as server-sent events. No SDK: httpx, which the Anthropic SDK already brings."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from julius.brain.providers import base
from julius.brain.providers.base import Capabilities, Done, TextDelta, ToolCall, Usage


def to_messages(request: dict[str, Any], forced: bool = True) -> list[dict[str, Any]]:
    """Messages API messages -> chat-completions messages. Tool results become role "tool" messages, in order.
    `forced`: the endpoint honours tool_choice; when it does not, the choice becomes an instruction in the system prompt."""
    out: list[dict[str, Any]] = []
    system = base.system_text(request.get("system"))
    if not forced:
        system += base.forced_tool_note(request.get("tool_choice"))
    if system:
        out.append({"role": "system", "content": system})
    for m in request.get("messages", []):
        parts = base.blocks(m.get("content"))
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in parts if b.get("type") == "text")
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
                }
                for b in parts
                if b.get("type") == "tool_use"
            ]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        rest = []
        for b in parts:
            if b.get("type") == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id", ""),
                        "content": base.tool_result_text(b.get("content")),
                    }
                )
            else:
                rest.append(b)
        if rest:
            out.append({"role": "user", "content": _user_content(rest)})
    return out


def _user_content(parts: list[dict[str, Any]]) -> Any:
    if not any(b.get("type") == "image" for b in parts):
        return "\n\n".join(b.get("text", "") for b in parts if b.get("type") == "text")
    content = []
    for b in parts:
        if b.get("type") == "text":
            content.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "image" and b.get("source", {}).get("type") == "base64":
            src = b["source"]
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"},
                }
            )
    return content


def to_tool_choice(choice: Any) -> Any:
    kind = (choice or {}).get("type")
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    if kind == "tool":
        return {"type": "function", "function": {"name": choice["name"]}}
    return None


def body(
    request: dict[str, Any], model: str, stream: bool, forced: bool = True, cap: int | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": model,
        "messages": to_messages(request, forced),
        "max_tokens": base.capped(request.get("max_tokens"), cap),
        "stream": stream,
    }
    tools = base.function_tools(request.get("tools"))
    if tools:
        out["tools"] = tools
        choice = to_tool_choice(request.get("tool_choice")) if forced else None
        if (request.get("tool_choice") or {}).get("type") == "none":
            choice = "none"
        if choice is not None:
            out["tool_choice"] = choice
    if stream:
        out["stream_options"] = {"include_usage": True}
    return out


def usage_of(raw: dict[str, Any] | None) -> Usage:
    raw = raw or {}
    cached = int(((raw.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    prompt = int(raw.get("prompt_tokens") or 0)
    return Usage(
        input_tokens=max(0, prompt - cached),
        output_tokens=int(raw.get("completion_tokens") or 0),
        cache_read_input_tokens=cached,
    )


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        capabilities: Capabilities | None = None,
        transport: Any = None,
        extra: dict[str, Any] | None = None,
        stats: dict[str, int] | None = None,
        limits: dict[str, Any] | None = None,
    ) -> None:
        self.model, self.base_url = model, base_url.rstrip("/")
        self.api_key, self.timeout = api_key, timeout
        self.capabilities = capabilities or Capabilities()
        self.transport = transport  # httpx transport (tests replay recorded responses through it)
        self.extra = dict(extra or {})  # provider-specific body fields from [models.<tier>] options
        self.stats = stats if stats is not None else {}
        self.limits = dict(limits or {})  # max_output_tokens, deadline_s

    def _client(self, timeout: Any = None):
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return httpx.Client(
            timeout=timeout or self.timeout, headers=headers, transport=self.transport
        )

    def events(self, request: dict[str, Any], stream: bool = False) -> Iterator[base.Event]:
        import httpx

        # always streamed, even for a one-shot call, so the deadline is checked while the model writes
        payload = (
            body(
                request,
                self.model,
                True,
                forced=self.capabilities.forced_tool_choice,
                cap=self.limits.get("max_output_tokens"),
            )
            | self.extra
        )
        deadline = base.Deadline(self.limits.get("deadline_s"))
        read = min(self.timeout, deadline.seconds) if deadline.seconds else self.timeout
        try:
            with (
                self._client(httpx.Timeout(read, connect=10.0)) as client,
                client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as r,
            ):
                if r.status_code >= 400:
                    r.read()
                _raise_for(r)
                if "json" in r.headers.get(
                    "content-type", ""
                ) and "event-stream" not in r.headers.get(
                    "content-type", ""
                ):  # a server that ignores "stream": one JSON reply
                    r.read()
                    yield from self._from_response(json.loads(r.text))
                    return
                yield from self._from_sse(r.iter_lines(), deadline)
        except httpx.TimeoutException as e:
            raise base.ProviderTimeout(f"{self.base_url}: no reply within {read:.0f} s") from e
        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            raise base.ProviderUnavailable(f"{self.base_url}: {type(e).__name__}") from e

    def _from_response(self, data: dict[str, Any]) -> Iterator[base.Event]:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        if msg.get("content"):
            yield TextDelta(msg["content"])
        calls = msg.get("tool_calls") or []
        for i, c in enumerate(calls):
            fn = c.get("function") or {}
            yield ToolCall(
                c.get("id") or f"call_{i + 1}",
                fn.get("name", ""),
                base.parse_arguments(fn.get("arguments"), self.stats),
            )
        yield Done(
            base.stop_reason(choice.get("finish_reason"), bool(calls)), usage_of(data.get("usage"))
        )

    def _from_sse(
        self, lines: Iterator[str], deadline: base.Deadline | None = None
    ) -> Iterator[base.Event]:
        partial: dict[int, dict[str, Any]] = {}
        finish, usage = None, None
        for line in lines:
            if deadline is not None:
                deadline.check()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield TextDelta(delta["content"])
                for tc in delta.get("tool_calls") or []:
                    slot = partial.setdefault(
                        int(tc.get("index", 0)), {"id": "", "name": "", "args": ""}
                    )
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["name"] = fn.get("name") or slot["name"]
                    slot["args"] += fn.get("arguments") or ""
                finish = choice.get("finish_reason") or finish
        for i in sorted(partial):
            slot = partial[i]
            yield ToolCall(
                slot["id"] or f"call_{i + 1}",
                slot["name"],
                base.parse_arguments(slot["args"], self.stats),
            )
        yield Done(base.stop_reason(finish, bool(partial)), usage_of(usage))


def _raise_for(r: Any) -> None:
    if r.status_code < 400:
        return
    detail = r.text[:200] if hasattr(r, "text") else ""
    if r.status_code in (401, 403):
        raise base.ProviderAuthError(f"HTTP {r.status_code}")
    if r.status_code == 500 and ("tool" in detail.lower() or "pars" in detail.lower()):
        raise base.ProviderBadOutput(f"HTTP 500: {detail}")
    if r.status_code in (408, 429) or r.status_code >= 500:
        raise base.ProviderUnavailable(f"HTTP {r.status_code}: {detail}")
    raise base.ProviderError(f"HTTP {r.status_code}: {detail}")
