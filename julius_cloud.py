"""Claude front end (Haiku 5.5) behind the same llm_chat shape Julius already uses.

Converts Julius/Ollama-style messages and tool schemas to the Messages API, streams text so speech
starts on the first sentence, caches the system prompt and tool schemas, records cost per response,
and falls back to a local model when the API is unreachable. An authentication error is spoken, not hidden.
"""
import json, os, threading, time
from types import SimpleNamespace
from pathlib import Path

import julius_config as cfg
import julius_costs as costs

AUTH_MESSAGE = "My API key expired or is invalid."
CAP_MESSAGE = "I've hit today's spending limit, so I'll stick to the quick model for the rest of the day."

# Per-tier request settings. Sampling parameters are omitted (rejected on these models); Haiku answers
# with thinking off at low effort for latency, Sonnet keeps adaptive thinking at medium effort.
TIERS = {
    "haiku": {"max_tokens": 1024, "output_config": {"effort": "low"}, "thinking": {"type": "disabled"}},
    "sonnet": {"max_tokens": 4096, "output_config": {"effort": "medium"}},
}

# Appended to Julius's system prompt in cloud mode so the front end knows when to escalate.
PLANNING_PROMPT = ("\n\nPLANNING\nYou have a planner: escalate_to_planner. Use it instead of answering yourself whenever a request "
                   "combines several lookups (for example weather and the time, or comparing two places), asks for a plan or itinerary, "
                   "needs research or comparison, or involves moving, sorting, organising, copying or renaming files. The planner can only "
                   "propose file changes; nothing happens until the user says yes, so never say a change was made. Pass the full task. "
                   "Its result is your answer: say it in your own voice, briefly. Answer one-step requests with your own tools.")

ESCALATE_SCHEMA = {"type": "function", "function": {
    "name": "escalate_to_planner",
    "description": ("Hand a multi-step task to the planner (a stronger model that can run sub-agents, look several things up "
                    "and propose file reorganisations). ALWAYS use it for: plans and itineraries, requests that combine several "
                    "lookups, research or comparisons, and organising/sorting/moving/renaming files. Do not use it for a simple fact "
                    "or a single tool call. Describe the task fully in your own words. The result is what you should say aloud."),
    "parameters": {"type": "object", "properties": {"task": {"type": "string", "description": "The full task, with every detail the planner needs"}},
                   "required": ["task"]}}}


def convert_tools(schemas):
    """Ollama/OpenAI function schemas -> Messages API tools; the last tool carries the cache breakpoint."""
    tools = [{"name": s["function"]["name"], "description": s["function"].get("description", ""),
              "input_schema": s["function"].get("parameters") or {"type": "object", "properties": {}}} for s in schemas]
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def convert_messages(messages):
    """Julius messages -> (system text, API messages). Tool calls get sequential ids that tool results consume in order."""
    system, out, ids, notes, counter = [], [], [], [], 0

    def add(role, blocks):
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role, content = m["role"], m.get("content") or ""
        if role == "system":
            (notes if out else system).append(content)
        elif role == "user":
            add("user", [{"type": "text", "text": content}])
        elif role == "assistant":
            blocks = [{"type": "text", "text": content}] if content.strip() else []
            for call in m.get("tool_calls") or []:
                fn = call.get("function", call)
                counter += 1
                ids.append(f"toolu_julius_{counter}")
                args = fn.get("arguments") or {}
                blocks.append({"type": "tool_use", "id": ids[-1], "name": fn["name"],
                               "input": json.loads(args) if isinstance(args, str) else dict(args)})
            if blocks:
                add("assistant", blocks)
        elif role == "tool":
            add("user", [{"type": "tool_result", "tool_use_id": ids.pop(0) if ids else "toolu_julius_0", "content": content}])
    for note in notes:  # mid-conversation system text rides along with the latest user turn
        add("user", [{"type": "text", "text": note}])
    return "\n\n".join(s for s in system if s), out


def _has_tool_blocks(api_messages):
    return any(b.get("type") in ("tool_use", "tool_result") for m in api_messages for b in m["content"])


def make_client(timeout=20.0, max_retries=1):
    import anthropic
    key = cfg.api_key()
    if not key:
        raise MissingKey()
    return anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=max_retries)


class MissingKey(Exception):
    pass


class CloudBrain:
    """Front-end brain: one instance per process. `offline(messages, tools, stream, options)` is the local fallback."""

    def __init__(self, config, tracker, client=None, offline=None, clock=time.monotonic):
        self.config, self.tracker, self._client, self.offline, self._clock = config, tracker, client, offline, clock
        self._slow = None
        self.model = config["models"]["haiku"]
        self.auth_bad = False
        self.auth_pending = False  # the spoken auth message has not been delivered through a streamed reply yet
        self.offline_until = 0.0
        self.cap_announced_day = None
        self.status = {"mode": "cloud"}

    # ---------- client and state ----------
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def slow_client(self):
        """Longer timeout for planner and sub-agent calls (non-streaming, adaptive thinking)."""
        if self._slow is None:
            self._slow = make_client(timeout=120.0, max_retries=2)
        return self._slow

    def reset_auth(self):
        self.auth_bad, self._client, self._slow = False, None, None

    def cap_notice(self):
        """Returns CAP_MESSAGE once per day when the spend cap is first seen reached."""
        if self.tracker.cap_reached():
            day = time.strftime("%Y-%m-%d")
            if self.cap_announced_day != day:
                self.cap_announced_day = day
                return CAP_MESSAGE
        return None

    def tool_schemas(self, schemas):
        """The stable full tool list (cache-friendly) plus escalate_to_planner unless the cap is reached."""
        if schemas is None:
            return None
        import julius_tools as jt
        full = list(jt.SCHEMAS)
        if not self.tracker.cap_reached():
            full.append(ESCALATE_SCHEMA)
        return full

    # ---------- request building ----------
    def request(self, messages, tools, max_tokens=None):
        system, api_messages = convert_messages(messages)
        kwargs = {"model": self.model, "messages": api_messages, "max_tokens": max_tokens or TIERS["haiku"]["max_tokens"],
                  "cache_control": {"type": "ephemeral"}, **{k: v for k, v in TIERS["haiku"].items() if k != "max_tokens"}}
        if system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        api_tools = convert_tools(self.tool_schemas(tools)) if tools is not None else None
        if api_tools:
            kwargs["tools"] = api_tools
        elif _has_tool_blocks(api_messages):  # history contains tool calls: tools must be declared, but not callable now
            import julius_tools as jt
            kwargs["tools"] = convert_tools(self.tool_schemas(jt.SCHEMAS))
            kwargs["tool_choice"] = {"type": "none"}
        return kwargs

    # ---------- public entry ----------
    def chat(self, messages, tools=None, stream=False, options=None):
        import anthropic
        if self.auth_bad:
            if self.auth_pending:  # speak the failure exactly once, on the reply the user actually hears
                if stream:
                    self.auth_pending = False
                    return iter([SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE))])
                return SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE, tool_calls=None))
            return self._offline(messages, tools, stream, options, announce=False)
        if self._clock() < self.offline_until:
            return self._offline(messages, tools, stream, options, announce=False)
        try:
            kwargs = self.request(messages, tools, max_tokens=int((options or {}).get("num_predict", 0)) or None)
            if stream:
                return self._stream(kwargs, messages, tools, options)
            response = self.client().messages.create(**kwargs)
            self.tracker.record("haiku", response.usage)
            return self._shape(response)
        except MissingKey:
            return self._say_auth(stream)
        except anthropic.AuthenticationError:
            return self._say_auth(stream)
        except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError, anthropic.InternalServerError):
            return self._go_offline(messages, tools, stream, options)

    # ---------- responses ----------
    @staticmethod
    def _shape(response):
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        calls = [SimpleNamespace(function=SimpleNamespace(name=b.name, arguments=dict(b.input)))
                 for b in response.content if getattr(b, "type", "") == "tool_use"]
        return SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls or None))

    def _stream(self, kwargs, messages, tools, options):
        import anthropic
        brain = self

        def parts():
            first = True
            try:
                with brain.client().messages.stream(**kwargs) as stream:
                    try:
                        for text in stream.text_stream:
                            first = False
                            yield SimpleNamespace(message=SimpleNamespace(content=text))
                    finally:
                        snapshot = getattr(stream, "current_message_snapshot", None)
                        if snapshot is not None and getattr(snapshot, "usage", None) is not None:
                            brain.tracker.record("haiku", snapshot.usage, note="stream")
            except (anthropic.AuthenticationError, MissingKey):
                brain.auth_bad, brain.auth_pending = True, False  # delivered right here
                yield SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE))
            except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.RateLimitError, anthropic.InternalServerError):
                if not first:
                    return  # partial reply already spoken; end it rather than repeat
                brain.offline_until = brain._clock() + brain.config["offline"]["retry_seconds"]
                yield from brain._offline(messages, tools, True, options, announce=True)
        return parts()

    # ---------- failure paths ----------
    def mark_auth_bad(self):
        self.auth_bad, self.auth_pending = True, True
        print("  [Anthropic API key missing or invalid; cloud brain disabled until restart]", flush=True)

    def _say_auth(self, stream):
        if not self.auth_bad:
            self.mark_auth_bad()
        if stream:  # a streamed reply is what gets spoken, so this delivers the message
            self.auth_pending = False
            return iter([SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE))])
        return SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE, tool_calls=None))

    def _go_offline(self, messages, tools, stream, options):
        self.offline_until = self._clock() + self.config["offline"]["retry_seconds"]
        return self._offline(messages, tools, stream, options, announce=True)

    def _offline(self, messages, tools, stream, options, announce):
        if self.offline is None:
            raise RuntimeError("Claude API unreachable and no offline model configured")
        if announce:
            print(f"  [Claude API unreachable; using local {self.config['offline']['model']}]", flush=True)
        return self.offline(messages, tools, stream, options)


_BRAIN = None
_TRACKER = None
_LOCK = threading.RLock()


def get_tracker():
    global _TRACKER
    with _LOCK:
        if _TRACKER is None:
            import julius_memory as jm
            config = cfg.load_config()
            path = Path(os.environ.get("JULIUS_COSTS_FILE") or Path(jm.MEM) / "costs.jsonl")
            _TRACKER = costs.CostTracker(path, config["spend"]["daily_cap_usd"])
        return _TRACKER


def get_brain(offline=None):
    global _BRAIN
    with _LOCK:
        if _BRAIN is None:
            _BRAIN = CloudBrain(cfg.load_config(), get_tracker(), offline=offline)
        elif offline is not None:
            _BRAIN.offline = offline
        return _BRAIN
