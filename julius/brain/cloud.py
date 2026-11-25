"""Claude front end (Haiku 5.5) behind the same llm_chat shape Julius already uses.

Converts Julius/Ollama-style messages and tool schemas to the Messages API, streams text so speech
starts on the first sentence, caches the system prompt and tool schemas, records cost per response,
and falls back to a local model when the API is unreachable. An authentication error is spoken, not hidden.
"""

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import julius.config as cfg
import julius.costs as costs
from julius import identity, log
from julius.brain import leak, providers
from julius.brain.providers.base import ProviderAuthError, ProviderBadOutput, ProviderUnavailable
from julius.costs import CostTracker

AUTH_MESSAGE = "My API key expired or is invalid."
CAP_MESSAGE = (
    "I've hit today's spending limit, so I'll stick to the quick model for the rest of the day."
)
FAILED_TURN_MESSAGE = "Sorry, my model didn't answer that time. Please try again."
UNREACHABLE_MESSAGE = (
    "I can't reach my model right now and I have no offline model, so I can't answer yet."
)
HARD_CAP_MESSAGE = "I've now used twice today's spending limit, so I'm switching to my offline model for the rest of the day."

# Per-tier request settings. Sampling parameters are omitted (rejected on these models); Haiku answers
# with thinking off at low effort for latency, Sonnet keeps adaptive thinking at medium effort.
TIERS = {
    "haiku": {
        "max_tokens": 1024,
        "output_config": {"effort": "low"},
        "thinking": {"type": "disabled"},
    },
    "sonnet": {"max_tokens": 4096, "output_config": {"effort": "medium"}},
}

# Appended to Julius's system prompt in cloud mode so the front end knows when to escalate.
PLANNING_PROMPT = identity.fill(
    "\n\nPLANNING\nYou have a planner: escalate_to_planner. Use it when the answer needs thinking across several facts, not just fetching them. "
    "Escalate for: (1) a plan, itinerary or schedule, e.g. 'plan a trip', 'plan a party'; (2) comparing options or making a "
    "recommendation, even when each fact is a single lookup, e.g. 'compare the weather in two cities', 'which is cheaper', 'should I "
    "buy X or Y'; (3) open-ended research, e.g. 'look into the best X' (but anything that says research, look up, find sources on or search the web for goes to research_task, never to you or the planner); a request that says plan or itinerary always escalates, even when it mentions the weather or the time; (4) a briefing or summary assembled from several sources; (5) any change "
    "to many files at once: moving, sorting, renaming, organising or cleaning up. Do NOT escalate a single fact, and do not escalate "
    "several independent lookups that only need reading out, e.g. 'the weather in A and the time in B': call every tool for those in the "
    "same reply and answer in one go. The planner can only propose file changes; nothing happens until the user says yes, so never "
    "say a change was made. Pass the full task. Its result is your answer: say it in your own voice, briefly."
    " Never describe your tool calls or what you are about to do internally ('I'll call the sprite tool now'): just call the tool and then answer."
    "\n\nTIE-BREAKS\n- Asked to summarize, brief or prioritise across several different topics ('summarize what I should look at: the NHL schedule, the injury report and a price chart'), "
    "that is a briefing: escalate_to_planner, even though each item is a lookup. Independent lookups he wants read out as asked (weather in A, time in B) are called together instead.\n"
    "- Choosing between options with a recommendation ('which is better for a road trip, A or B? check the forecast for both and recommend one') is escalate_to_planner right away, even though each part is a lookup: never call the lookups yourself first.\n"
    "- 'Forget X' or 'forget what you know about X' is forget_fact(topic=X) right away; list_memories is only for 'what do you remember', never a step before forgetting.\n"
    "- 'This' (as the thing to change or measure), 'this picture', 'this photo', 'my selfie' or 'this track' with no file name means the one he dropped on the orb: call the tool with source 'dropped' (it asks if nothing was dropped) and do not ask for a file name first.\n"
    "- Making his own picture look like a comic book, pencil sketch, watercolor, cartoon, pop art or pixel art, or showing every style side by side, is image_style on that picture (style 'all' for every style); it never draws anything from nothing. 'Make me a sprite', 'a game sprite of me', 'turn me into a Game Boy character', 'make this photo look like a retro game character' or any sprite or game character of him means his own photo: call image_style right away with style 'sprite' and source 'dropped' (the tool asks for a picture if none was dropped; never ask him first). 'Pixel art', 'pixelate' and a Game Boy or retro LOOK or palette on a photo stay style 'pixel_art'; only the words sprite, character or avatar make style 'sprite'."
    "\n- Changing the sprite he already has ('bigger beard', 'taller afro', 'add glasses', 'close-set eyes', 'give him a cap', 'undo that', 'reopen my sprite') is sprite_edit with his words; making a sprite from a photo is image_style."
    "\n- Saving ('save it', 'save as Rex's main sprite', 'save this as X', 'call it X', 'name it X') is save_card, with the name when he gives one; you can always save his cards, so never say you have no way to save. Making a sprite his main one, in any words ('make this my main sprite', 'this should be the main one from now on'), is sprite_edit with change 'make this my main sprite', never save_card; 'open my main sprite' is sprite_edit too."
    "\n- His conversations with you (new conversation, go back to the chat about X, what were we talking about in X, rename, pin, move to a project, delete, restore, note for this chat or project) are the conversations tool; 'remember X' about him is still remember_fact, and files are file tools. What was said or decided in an earlier conversation or chat ('remind me what we settled on in that palette chat') is conversations with action recap: never answer it from memory or say you don't remember."
    "\n- SESSION STATE in the request is {name}'s own record of this session (the current image, the current sprite, what was made): trust it. Never ask for a photo when it lists a current image (call the tool with source 'dropped'), and never deny an action it lists. In an active context read short or unclear words toward it: after a palette question 'Nes' or 'Miss' means the NES palette; 'yes, sprite' means the current sprite. Ask at most one clarifying question; if the answer is still unclear, act on the most likely reading and say how to undo. When he says you made or did something, check SESSION STATE: if it is there, confirm it plainly and carry on; an earlier reply of yours that said otherwise was wrong, so say so briefly."
)

CAPABILITIES_PROMPT = identity.fill(
    "\n\nWHAT YOU CAN DO\nNever say you cannot do any of these. If a request for one reaches you, use its tool; do not decline, and never claim something happened unless a result says so.\n"
    "- Files in Documents, Desktop, Downloads and the user's configured folders such as sandbox: find, list, read, move, copy, rename, create, move to the trash (recoverable; you never delete permanently), undo, empty the trash, and say what changed. "
    "Call file_request with his own words, also for undo and 'what did you change'. The file controller always shows a preview first and nothing changes until the user says yes. Changes to MANY files at once (organising, sorting, cleaning up, renaming or moving everything of a kind) still go to escalate_to_planner.\n"
    "- Coding: a sandboxed coding agent fixes or writes code in a COPY of a project under his dev folder. Call code_request right away, never just describe how it works. It needs his yes to start and a separate yes to merge; he can say show the changes, merge it, undo the merge, cancel the coding task.\n"
    "- Memory: remember_fact, forget_fact, list_memories. Reminders, timers, tasks and events: set_reminder, list_reminders, cancel_reminder, list_planner_items.\n"
    "- Saving: save_card saves a side-panel card (a sprite, pixel art, a photo style, a selfie, a chart, research) to his Julius Saved folder, with an optional name.\n- Charts and tables: render_chart shows a bar, line or pie chart or a table in the side panel from data you have (never invented): get the numbers first with the right tool (for example get_quote with days for a price history, or research_task), then call render_chart. You MUST call it to show one: never say a chart or table is in the side panel unless render_chart's result says so. Then say in one sentence what it shows.\n"
    "- Everyday single lookups (weather, time, math, conversions, quotes, scores, news, web search) use their own tools.\n"
    "- Comparisons, recommendations, plans, briefings that combine several facts, research, and changes to many files at once all go to escalate_to_planner, even when each fact is a simple lookup.\n"
    'If the user says only yes, do it or go ahead and you did not just ask him a question, nothing is waiting for a yes: say exactly "There\'s nothing waiting for a yes right now. What would you like me to do?" and never invent a result.'
)

ESCALATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "escalate_to_planner",
        "description": (
            "Hand a task that needs thinking, not just fetching, to the planner (a stronger model with sub-agents). "
            "Use for plans, comparisons and recommendations, research, briefings and bulk file work, as described in PLANNING. "
            "Never for one fact, one tool call, or independent lookups. Describe the task fully; the result is what you say aloud."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The full task, with every detail the planner needs",
                }
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
}


def convert_tools(schemas: Any) -> Any:
    """Ollama/OpenAI function schemas -> Messages API tools; the last tool carries the cache breakpoint."""
    tools = [
        {
            "name": s["function"]["name"],
            "description": s["function"].get("description", ""),
            "input_schema": s["function"].get("parameters") or {"type": "object", "properties": {}},
        }
        for s in schemas
    ]
    for tool in tools:
        if tool["name"] == "escalate_to_planner":
            tool["strict"] = True  # schema-valid arguments: the front end must always send a task
    if tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def convert_messages(messages: list[dict[str, Any]]) -> Any:
    """Julius messages -> (system text, API messages). Tool calls get sequential ids that tool results consume in order."""
    system, out, ids, notes, counter = [], [], [], [], 0

    def add(role: Any, blocks: Any) -> None:
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
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": ids[-1],
                        "name": fn["name"],
                        "input": json.loads(args) if isinstance(args, str) else dict(args),
                    }
                )
            if blocks:
                add("assistant", blocks)
        elif role == "tool":
            add(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": ids.pop(0) if ids else "toolu_julius_0",
                        "content": content,
                    }
                ],
            )
    for note in notes:  # mid-conversation system text rides along with the latest user turn
        add("user", [{"type": "text", "text": note}])
    return "\n\n".join(s for s in system if s), out


def _has_tool_blocks(api_messages: Any) -> Any:
    return any(
        b.get("type") in ("tool_use", "tool_result") for m in api_messages for b in m["content"]
    )


def make_client(timeout: float = 20.0, max_retries: int = 1) -> Any:
    import anthropic

    key = cfg.api_key()
    if not key:
        raise MissingKey()
    return anthropic.Anthropic(api_key=key, timeout=timeout, max_retries=max_retries)


class MissingKey(Exception):
    pass


def _personalize_tools(tools: list[dict[str, Any]]) -> Any:
    """Tool definitions with the author's name and city replaced by this install's profile in every description."""
    from julius import profile

    def walk(x: Any) -> Any:
        if isinstance(x, dict):
            return {
                k: (profile.apply(v) if k == "description" and isinstance(v, str) else walk(v))
                for k, v in x.items()
            }
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(tools)


class CloudBrain:
    """Front-end brain: one instance per process. `offline(messages, tools, stream, options)` is the local fallback."""

    def __init__(
        self,
        config: dict[str, Any],
        tracker: CostTracker,
        client: Any | None = None,
        offline: Any | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config, self.tracker, self._client, self.offline, self._clock = (
            config,
            tracker,
            client,
            offline,
            clock,
        )
        self._slow = None
        self.front_end = providers.resolve("front_end", config)
        self.model = self.front_end.model
        self.auth_bad = False
        self.auth_pending = (
            False  # the spoken auth message has not been delivered through a streamed reply yet
        )
        self.offline_until = 0.0
        self.failures = 0  # consecutive unavailable/timeout failures; the offline window opens only after several
        self.ignore_offline_window = (
            False  # eval brains: a failure fails that turn only, never later turns
        )
        self.announced = {}
        self.status = {"mode": "cloud"}
        self._last_tools = None

    # ---------- client and state ----------
    def client(self) -> Any:
        """The front end's client (short timeout, streamed): the Anthropic SDK or, for another provider, the shim."""
        if self._client is None:
            self._client = providers.client(self.front_end, 20.0, 1, make_client)
        return self._client

    def slow_client(self) -> Any:
        """Longer timeout for planner and sub-agent calls (non-streaming, adaptive thinking) on Anthropic."""
        if self._slow is None:
            from julius.brain.providers.anthropic import TaggingClient

            self._slow = TaggingClient(make_client(timeout=120.0, max_retries=2))
        return self._slow

    def tier_model(self, tier: str, legacy: str | None = None) -> providers.TierModel:
        return providers.resolve(tier, self.config, legacy)

    def client_for(self, tier: str, legacy: str | None = None) -> Any:
        """The client for a tier's slow calls (planner, research, vision, conversation titles): Anthropic's shared slow
        client, or the tier's own provider."""
        tm = self.tier_model(tier, legacy)
        if tm.is_anthropic:
            return self.slow_client()
        return providers.client(tm, 120.0, 2, make_client)

    def model_for(self, tier: str, legacy: str | None = None) -> str:
        return self.tier_model(tier, legacy).model

    def reset_auth(self) -> None:
        self.auth_bad, self._client, self._slow = False, None, None
        providers.reset()

    def cap_notice(self) -> Any:
        """The spoken notice for a spend threshold, once per day each: the cap (Haiku only), then twice the cap (offline)."""
        day = time.strftime("%Y-%m-%d")
        for key, reached, message in (
            ("hard", self.tracker.offline_required(), HARD_CAP_MESSAGE),
            ("cap", self.tracker.cap_reached(), CAP_MESSAGE),
        ):
            if reached and self.announced.get(key) != day:
                self.announced.update({key: day, "cap": day} if key == "hard" else {key: day})
                return message
        return None

    def tool_schemas(self, schemas: Any) -> Any:
        """The full tool list (plus escalate_to_planner unless the cap is reached) whenever tools are offered.

        Trimming schemas was a local-model speed fix. Here one stable list is cached once and shared by every turn
        and follow-up, which costs less than rewriting per-subset caches. The offline fallback still gets the
        caller's trimmed list.
        """
        if schemas is None:
            return None
        import julius.brain.tools as jt

        chosen = list(jt.SCHEMAS)
        if not self.tracker.cap_reached():
            chosen.append(ESCALATE_SCHEMA)
        self._last_tools = chosen
        return chosen

    # ---------- request building ----------
    def request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: Any | None = None,
        force_tool: bool = False,
        force_planner: bool = False,
        force_research: bool = False,
        force_name: str = "",
    ) -> Any:
        system, api_messages = convert_messages(messages)
        from julius import profile

        if system:
            system = profile.apply(
                system
            )  # the author's name and city in the prompts become this install's [profile]
        kwargs = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": max_tokens or TIERS["haiku"]["max_tokens"],
            "cache_control": {"type": "ephemeral"},
            **{k: v for k, v in TIERS["haiku"].items() if k != "max_tokens"},
        }
        if system:
            static, marker, state = system.partition("\n\nSESSION STATE (")
            kwargs["system"] = [
                {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}}
            ] + (
                [{"type": "text", "text": "SESSION STATE (" + state}] if marker else []
            )  # per turn: after the cached prefix
            if (
                not self.front_end.is_anthropic
            ):  # small local models recite their prompt unless told not to, last
                kwargs["system"].append({"type": "text", "text": leak.PRIVATE_NOTE})
        api_tools = convert_tools(self.tool_schemas(tools)) if tools is not None else None
        if api_tools:
            api_tools = _personalize_tools(api_tools)
        if api_tools:
            kwargs["tools"] = api_tools
            if force_research and any(t["name"] == "research_task" for t in api_tools):
                kwargs["tool_choice"] = {"type": "tool", "name": "research_task"}
            elif force_name and any(t["name"] == force_name for t in api_tools):
                kwargs["tool_choice"] = {"type": "tool", "name": force_name}
            elif force_planner and any(t["name"] == "escalate_to_planner" for t in api_tools):
                kwargs["tool_choice"] = {"type": "tool", "name": "escalate_to_planner"}
            elif force_tool:
                kwargs["tool_choice"] = {
                    "type": "any"
                }  # a live-data question: answer from a tool call made in THIS turn
        elif _has_tool_blocks(
            api_messages
        ):  # history contains tool calls: tools must be declared, but not callable now
            import julius.brain.tools as jt

            # Reuse this turn's tool list so the cached system+tools prefix from the tool phase is hit again.
            kwargs["tools"] = _personalize_tools(
                convert_tools(self._last_tools or self.tool_schemas(jt.SCHEMAS))
            )
            kwargs["tool_choice"] = {"type": "none"}
        return kwargs

    # ---------- public entry ----------
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        options: dict[str, Any] | None = None,
    ) -> Any:
        import anthropic

        options = dict(options or {})
        force_tool = bool(
            options.pop("force_tool", False)
        )  # cloud-only request flag; never passed on to the local fallback
        force_planner = bool(options.pop("force_planner", False))
        force_research = bool(options.pop("force_research", False))
        force_name = str(options.pop("force_name", "") or "")
        if self.auth_bad:
            if (
                self.auth_pending
            ):  # speak the failure exactly once, on the reply the user actually hears
                if stream:
                    self.auth_pending = False
                    return iter([SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE))])
                return SimpleNamespace(
                    message=SimpleNamespace(content=AUTH_MESSAGE, tool_calls=None)
                )
            return self._offline(messages, tools, stream, options, announce=False)
        if (
            self._clock() < self.offline_until and not self.ignore_offline_window
        ) or self.tracker.offline_required():
            return self._offline(messages, tools, stream, options, announce=False)
        try:
            kwargs = self.request(
                messages,
                tools,
                force_tool=force_tool,
                force_planner=force_planner,
                force_research=force_research,
                force_name=force_name,
            )  # length is steered by the prompt (brief by default, longer when asked); the local 160-token cap would cut a requested long answer mid-sentence
            if stream:
                return self._stream(kwargs, messages, tools, options)
            response = self.client().messages.create(**kwargs)
            self.failures = 0
            self.tracker.record("haiku", response.usage, **self._ledger())
            return self._guarded(self._shape(response), kwargs)
        except MissingKey:
            return self._say_auth(stream)
        except (anthropic.AuthenticationError, ProviderAuthError):
            return self._say_auth(stream)
        except ProviderBadOutput as e:  # the server is fine; the model's reply was unusable
            log.console(f"  [{self._who()}: unusable reply ({e}); this turn only]", flush=True)
            return self._say(FAILED_TURN_MESSAGE, stream, failed=True)
        except (
            ProviderUnavailable,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ):
            return self._failed(messages, tools, stream, options)

    # ---------- responses ----------
    def _guard(self, kwargs: dict[str, Any]) -> leak.LeakGuard | None:
        """The prompt-recital check, for front ends that are not Anthropic (see julius/brain/leak.py)."""
        if self.front_end.is_anthropic or not kwargs.get("system"):
            return None
        return leak.LeakGuard("\n".join(b["text"] for b in kwargs["system"]))

    def _guarded(self, shaped: Any, kwargs: dict[str, Any]) -> Any:
        guard = self._guard(kwargs)
        if guard is not None and shaped.message.content:
            text, dropped = guard.clean(shaped.message.content)
            if dropped:
                log.console(
                    "  [dropped a reply sentence that recited the system prompt]", flush=True
                )
            shaped.message.content = text
        return shaped

    @staticmethod
    def _shape(response: Any) -> Any:
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        calls = [
            SimpleNamespace(function=SimpleNamespace(name=b.name, arguments=dict(b.input)))
            for b in response.content
            if getattr(b, "type", "") == "tool_use"
        ]
        return SimpleNamespace(message=SimpleNamespace(content=text, tool_calls=calls or None))

    def _stream(
        self,
        kwargs: Any,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: dict[str, Any],
    ) -> Any:
        import anthropic

        brain = self
        guard = self._guard(kwargs)

        def parts():
            first = True
            try:
                with brain.client().messages.stream(**kwargs) as stream:
                    try:
                        texts = stream.text_stream
                        if guard is not None:
                            texts = guard.stream(texts)
                        for text in texts:
                            first = False
                            yield SimpleNamespace(message=SimpleNamespace(content=text))
                        final = (
                            stream.get_final_message()
                        )  # a tool_use block arrives only once the reply is complete
                        tool_calls = [
                            SimpleNamespace(
                                function=SimpleNamespace(name=b.name, arguments=dict(b.input))
                            )
                            for b in final.content
                            if getattr(b, "type", "") == "tool_use"
                        ]
                        if tool_calls:
                            yield SimpleNamespace(
                                message=SimpleNamespace(content="", tool_calls=tool_calls)
                            )
                        brain.failures = 0
                    finally:
                        snapshot = getattr(stream, "current_message_snapshot", None)
                        if snapshot is not None and getattr(snapshot, "usage", None) is not None:
                            brain.tracker.record(
                                "haiku", snapshot.usage, note="stream", **brain._ledger()
                            )
            except (anthropic.AuthenticationError, ProviderAuthError, MissingKey):
                brain.auth_bad, brain.auth_pending = True, False  # delivered right here
                yield SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE))
            except ProviderBadOutput as e:
                log.console(f"  [{brain._who()}: unusable reply ({e}); this turn only]", flush=True)
                if first:
                    yield from brain._say(FAILED_TURN_MESSAGE, True, failed=True)
            except (
                ProviderUnavailable,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                anthropic.RateLimitError,
                anthropic.InternalServerError,
            ):
                if not first:
                    brain.failures += 1
                    return  # partial reply already spoken; end it rather than repeat
                yield from brain._failed(messages, tools, True, options)

        return parts()

    def _ledger(self) -> dict[str, str]:
        """The front end's provider and model for the cost ledger (a streamed reply's usage carries no tag of its own)."""
        return {"provider": self.front_end.provider, "model": self.front_end.model}

    # ---------- failure paths ----------
    def mark_auth_bad(self) -> None:
        self.auth_bad, self.auth_pending = True, True
        who = (
            "Anthropic API key" if self.front_end.is_anthropic else f"{self.front_end.provider} key"
        )
        log.console(
            f"  [{who} missing or invalid; cloud brain disabled until restart]",
            flush=True,
        )

    def _say_auth(self, stream: Any) -> Any:
        if not self.auth_bad:
            self.mark_auth_bad()
        if stream:  # a streamed reply is what gets spoken, so this delivers the message
            self.auth_pending = False
            return iter([SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE))])
        return SimpleNamespace(message=SimpleNamespace(content=AUTH_MESSAGE, tool_calls=None))

    def _failed(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stream: Any,
        options: dict[str, Any],
    ) -> Any:
        """The model timed out or could not be reached. One failure fails only this turn, with a spoken message; after
        [offline] failures_before_offline in a row the offline window opens and the offline model answers (when there is
        one). An eval brain never opens the window, so one slow case cannot fail the cases after it."""
        self.failures += 1
        limit = int(self.config["offline"].get("failures_before_offline", 2))
        if self.ignore_offline_window or self.failures < limit:
            log.console(
                f"  [{self._who()} did not answer (failure {self.failures} of {limit} before going offline)]",
                flush=True,
            )
            return self._say(FAILED_TURN_MESSAGE, stream, failed=True)
        self.offline_until = self._clock() + self.config["offline"]["retry_seconds"]
        return self._offline(messages, tools, stream, options, announce=True)

    def _who(self) -> str:
        return "Claude API" if self.front_end.is_anthropic else self.front_end.describe()

    @staticmethod
    def _say(text: str, stream: Any, failed: bool = False) -> Any:
        """A spoken reply that is not the model's (a failure notice): marked failed=True so evals can tell it apart."""
        msg = SimpleNamespace(content=text, tool_calls=None, failed=failed)
        if stream:
            return iter([SimpleNamespace(message=msg)])
        return SimpleNamespace(message=msg)

    def _offline(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stream: Any,
        options: dict[str, Any],
        announce: Any,
    ) -> Any:
        if self.offline is None:
            log.console(
                f"  [{self._who()} unreachable and no offline model configured]", flush=True
            )
            return self._say(UNREACHABLE_MESSAGE, stream, failed=True)
        if announce:
            log.console(
                f"  [{self._who()} unreachable; using local {self.config['offline']['model']}]",
                flush=True,
            )
        return self.offline(messages, tools, stream, options)


_BRAIN = None
_TRACKER = None
_LOCK = threading.RLock()


def get_tracker() -> Any:
    global _TRACKER
    with _LOCK:
        if _TRACKER is None:
            import julius.memory.sessions as jm

            config = cfg.load_config()
            path = Path(os.environ.get("JULIUS_COSTS_FILE") or Path(jm.MEM) / "costs.jsonl")
            _TRACKER = costs.CostTracker(
                path,
                config["spend"]["daily_cap_usd"],
                offline_multiple=config["spend"]["offline_at_multiple"],
            )
        return _TRACKER


def get_brain(offline: Any | None = None) -> Any:
    global _BRAIN
    with _LOCK:
        if _BRAIN is None:
            _BRAIN = CloudBrain(cfg.load_config(), get_tracker(), offline=offline)
        elif offline is not None:
            _BRAIN.offline = offline
        return _BRAIN
