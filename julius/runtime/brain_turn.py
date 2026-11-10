"""One request to the brain: shortcuts, tool round, streamed spoken answer, stall recovery."""

import re
import time
from dataclasses import dataclass
from types import SimpleNamespace

import julius.brain.tools as jt
from julius.audio import ack
from julius.audio.speech_text import EXACT_REQUEST, speakable
from julius.brain import livedata
from julius.brain.backend import llm_chat
from julius.brain.conversation import (
    authority_discussion_reply,
    conversation_messages,
    voice_capability_reply,
)
from julius.runtime.session import Session
from julius.runtime.speech import print_tool
from julius.runtime.text import (
    BAD_ID,
    CURRENT_TIME_QUERY,
    META_LEAK,
    SCHEDULE_QUERY,
    STALL,
    TOOL_HINT,
    repair_history,
    schedule_league,
)

LONG_RESULT_NOTE = (
    "The full tool result is already printed on screen. "
    "Answer aloud in at most two short sentences: give the headline "
    "and only two or three highlights. Do not list every item, "
    "do not use markdown, and do not add a generic closing offer."
)
STALL_NOTE = (
    "You just stalled. You cannot do anything between messages. Call the "
    "tool NOW and give the real answer in this reply, or say plainly "
    "that you can't."
)
SENTENCE_END = re.compile(r"[.!?…\n](\s|$)")


def _say(text: str, speak_fn) -> str:
    """Print and speak a ready-made answer."""
    print(f"  JULIUS: {text}", flush=True)
    if speak_fn:
        speak_fn(text)
    return text


def _unique_calls(calls) -> list:
    """The model sometimes repeats an identical call in one turn; keep each distinct call once."""
    unique = {}
    for call in calls:
        key = (call.function.name, repr(sorted(dict(call.function.arguments).items())))
        unique.setdefault(key, call)
    return list(unique.values())


def _assistant_message(content: str, calls) -> dict:
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {"function": {"name": c.function.name, "arguments": dict(c.function.arguments)}}
            for c in calls
        ],
    }


def _is_schedule_question(name: str, user_text: str) -> bool:
    """'What time does the Sharks game start?' is a schedule question; the clock does not answer it."""
    return (
        name == "get_datetime"
        and bool(SCHEDULE_QUERY.search(user_text))
        and not CURRENT_TIME_QUERY.search(user_text)
    )


def _schedule_answer(user_text: str, out: str, args: dict) -> tuple[str, dict]:
    """Replace a clock answer to a schedule question with that league's scores, or say why not."""
    league = schedule_league(user_text)
    if league:
        args = {"league": league, "day": "today"}
        return jt.call("get_scores", args), args
    return (
        "That only gives the current time, not when a game starts, and I can't tell which "
        "team or league you mean. Ask again with the team name.",
        args,
    )


@dataclass
class StreamState:
    """What one streamed reply has produced so far."""

    full: str = ""
    pending: str = ""
    spoken: int = 0


class BrainTurn:
    """The state of one `ask_streaming` call (the closures of the old function, made explicit)."""

    def __init__(self, s: Session, user_text, speak_fn, before_inference, tool_carry, nudge):
        self.s = s
        self.user_text = user_text
        self.speak_fn = speak_fn
        self.before_inference = before_inference
        self.tool_carry = tool_carry
        self.nudge = nudge
        self.messages: list[dict] = []
        self.prepared = False
        self.stream_calls: list = []
        self.stalled = False
        self.long_tool_result = False
        self.dropped_ungrounded: list[int] = []
        # "On it." when a tool call outlasts 1.5 s with nothing said yet (streamed speech only).
        self.ack = ack.Acknowledger(speak_fn, enabled=s.cloud)
        self.live_query = livedata.is_live_query(user_text)

    # --- preparation -------------------------------------------------------------------------
    def build_messages(self, vision_line: str) -> None:
        """The request plus recalled notes and any dropped-file block (sent to the model only)."""
        s = self.s
        notes = s.memory.block(self.user_text) if s.memory is not None else ""
        if s.drop_stage is not None:  # files dropped on the orb: a labelled, data-only selection
            block = s.drop_stage.model_block()
            if block:
                notes = (notes + "\n\n" if notes else "") + block
        repair_history(s.history)
        s.unknown_tool_retried = False
        content = self.user_text + (f"\n\n{notes}" if notes else "")
        self.messages = list(s.history) + [{"role": "user", "content": content}]
        s.turn_messages = (self.messages, len(self.messages))  # kept in the history afterwards
        # The cloud brain gets the full tool list every turn; local models keep the keyword hints.
        self.hinted = bool(self.tool_carry or TOOL_HINT.search(self.user_text) or s.cloud)
        if not self.hinted and not self.nudge and not vision_line:
            self.messages = conversation_messages(
                self.messages, s.memory_context, vision_enabled=s.vision_enabled
            )

    def prepare_inference(self) -> None:
        """Check for cancellation; run the caller's pre-inference hook once."""
        if self.s.cancellation:
            self.s.cancellation.check()
        if not self.prepared:
            if self.before_inference:
                self.before_inference()
            self.prepared = True

    # --- direct intent -----------------------------------------------------------------------
    def direct_answer(self) -> str | None:
        """A deterministic live-intent answer, spoken from the tool result with no model call."""
        s = self.s
        direct = s.intent_router.resolve(self.user_text)
        if not direct:
            return None
        name, args = direct
        out = jt.call(name, args)
        print(f"\r  [tool: {name}({args})]", flush=True)
        print_tool(name, out)
        s.intent_router.record(name, args, out)
        s.tool_carry_next = True
        return _say(s.intent_router.spoken_result(name, out, args), self.speak_fn)

    # --- streaming ---------------------------------------------------------------------------
    def grounded(self, messages) -> bool:
        return livedata.tool_result_this_turn(messages) or bool(self.stream_calls)

    def speak_sentences(self, st: StreamState, messages) -> None:
        """Print and speak every complete sentence waiting in `st.pending`."""
        while True:
            match = SENTENCE_END.search(st.pending)
            if not match:
                return
            sent, st.pending = st.pending[: match.end()].strip(), st.pending[match.end() :]
            if META_LEAK.search(sent):
                sent = ""  # the model narrating its own instructions is never spoken
            if sent and livedata.blocked(sent, self.live_query, self.grounded(messages)):
                sent = ""
                self.dropped_ungrounded.append(1)  # a changing figure with no tool result behind it
            if len(sent) > 2:
                st.spoken += 1
                self.ack.said_something()
                print(sent, end=" ", flush=True)  # print BEFORE speaking
                if self.speak_fn and (not self.long_tool_result or st.spoken <= 2):
                    self.speak_fn(sent)

    def finish_stream(self, st: StreamState, messages) -> str:
        """Speak what is left, and say so once if figures were withheld and no tool was called."""
        rest = st.pending.strip()
        if rest and livedata.blocked(rest, self.live_query, self.grounded(messages)):
            st.pending = rest = ""
            self.dropped_ungrounded.append(1)
        if rest:
            print(rest, end="", flush=True)
            if self.speak_fn and (not self.long_tool_result or st.spoken < 2):
                self.speak_fn(rest)
        if self.dropped_ungrounded and not self.stream_calls:
            self.dropped_ungrounded.clear()
            print(livedata.UNGROUNDED_LINE, end="", flush=True)
            if self.speak_fn:
                self.speak_fn(livedata.UNGROUNDED_LINE)
            st.full = livedata.UNGROUNDED_LINE
        print("\n", flush=True)
        return st.full

    def stream(self, messages, allow_retry=True, tools=None, force_tool=False) -> str | None:
        """Stream one reply; sentences are spoken as they complete. None means it stalled."""
        s = self.s
        self.prepare_inference()
        print("\r\033[K  JULIUS: ", end="", flush=True)
        st = StreamState()
        self.stream_calls.clear()
        kwargs = {"tools": tools} if tools is not None else {}
        options = {"temperature": 0.4, "num_predict": 160}
        if force_tool:
            options["force_tool"] = True
        parts = llm_chat(messages=messages, think=False, stream=True, **kwargs, options=options)
        try:
            for part in parts:
                if s.cancellation:
                    s.cancellation.check()
                self.stream_calls.extend(getattr(part.message, "tool_calls", None) or [])
                token = part.message.content or ""
                if token and s.first_token_at is None:
                    s.first_token_at = time.perf_counter()
                st.full += token
                st.pending += token
                if (
                    allow_retry
                    and st.spoken == 0
                    and not self.stream_calls
                    and STALL.search(st.full)
                ):
                    return None  # abort before anything is spoken
                self.speak_sentences(st, messages)
        finally:
            closer = getattr(parts, "close", None)
            if callable(closer):
                closer()
        return self.finish_stream(st, messages)

    # --- tool round --------------------------------------------------------------------------
    def model_tool_message(self):
        """The model's reply to the request with tools attached (streamed for the cloud brain)."""
        s = self.s
        schemas = jt.select_schemas(self.user_text)
        if s.cloud:
            force = jt.live_tool_required(self.user_text) and not livedata.tool_result_this_turn(
                self.messages
            )
            text = self.stream(self.messages, tools=schemas, force_tool=force)
            if text is None:
                return None
            return SimpleNamespace(content=text, tool_calls=list(self.stream_calls))
        reply = llm_chat(
            messages=self.messages,
            tools=schemas,
            think=False,
            options={"temperature": 0.0, "num_predict": 256},
        )
        return reply.message

    def run_calls(self, calls) -> list[tuple[str, bool]]:
        """Run the tool calls (several at once when parallel-safe); a (result, ok) pair for each."""
        with self.ack.watch():
            batch = None
            if len(calls) > 1:
                batch = jt.call_many([(c.function.name, dict(c.function.arguments)) for c in calls])
            if batch is None:
                call = calls[0]
                out = jt.call(call.function.name, dict(call.function.arguments))
                batch = [(out, jt.dispatcher().last_result["ok"])]
        return batch

    def failed_round(self, calls, batch) -> str | None:
        """Every call failed: give each a result, and either let the model retry or say so."""
        s = self.s
        out = batch[0][0]
        print_tool(calls[0].function.name, out)
        unknown = [
            c.function.name
            for c, (result, _) in zip(calls, batch, strict=False)
            if result.startswith(f"{c.function.name} failed: KeyError")
        ]
        for c, (result, _) in zip(calls, batch, strict=False):
            self.messages.append({"role": "tool", "name": c.function.name, "content": result})
        if unknown and not s.unknown_tool_retried:
            # The model invented a tool name. Tell it so; never read "KeyError" aloud.
            s.unknown_tool_retried = True
            self.messages[-1]["content"] = (
                f"There is no tool named {unknown[0]}. "
                "Answer without it, or say plainly that you can't do that."
            )
            return None
        if unknown:
            out = "Sorry, I couldn't do that one."
        s.unknown_tool_retried = False
        if self.speak_fn:
            self.speak_fn(out)
        return out

    def record_results(self, calls, batch):
        """Add each tool result to the messages; returns the last (name, result, args)."""
        s = self.s
        name = out = args = None
        for call, (out, ok) in zip(calls, batch, strict=False):
            name, args = call.function.name, dict(call.function.arguments)
            if _is_schedule_question(name, self.user_text):
                out, args = _schedule_answer(self.user_text, out, args)
            print_tool(name, out)
            if ok:
                s.intent_router.record(name, args, out)
            if out.count("\n") > 3:
                self.long_tool_result = True
            self.messages.append({"role": "tool", "name": call.function.name, "content": out})
        return name, out, args

    def ready_sentence(self, calls, name, out, args) -> str | None:
        """One simple lookup (weather, time, calculator, price): a sentence ready to speak."""
        if len(calls) != 1 or _is_schedule_question(name, self.user_text):
            return None
        exact = bool(EXACT_REQUEST.search(self.user_text))
        ready = speakable(name, out, args, exact=exact)
        if not ready:
            return None
        print(f"\r  JULIUS: {ready}", flush=True)
        if self.speak_fn:
            self.speak_fn(ready)
        return ready

    def tool_round(self) -> str | None:
        """One tool round: a turn that needs the model's summary is two calls (tool, then answer).
        Returns the final answer when the round already produced it."""
        self.prepare_inference()
        message = self.model_tool_message()
        if message is None:
            self.stalled = True
            return None
        if self.s.cloud and not message.tool_calls:
            return message.content
        calls = _unique_calls(message.tool_calls or [])
        if not calls:
            return None
        self.messages.append(_assistant_message(message.content, calls))
        for call in calls:
            print(
                f"\r  [tool: {call.function.name}({dict(call.function.arguments)})]          ",
                flush=True,
            )
        self.s.tool_carry_next = True
        batch = self.run_calls(calls)
        if not any(ok for _, ok in batch):
            return self.failed_round(calls, batch)
        name, out, args = self.record_results(calls, batch)
        print("  ...thinking", end="", flush=True)
        return self.ready_sentence(calls, name, out, args)

    # --- recovery ----------------------------------------------------------------------------
    def recover_from_stall(self) -> str:
        """The model said "let me check" instead of acting: make it call a tool now."""
        s = self.s
        print("\r  [stall detected — forcing tool use]        ", flush=True)
        self.messages.append({"role": "system", "content": STALL_NOTE})
        reply = llm_chat(
            messages=self.messages,
            tools=jt.select_schemas(self.user_text),
            think=False,
            options={"temperature": 0.3, "num_predict": 200},
        )
        for call in reply.message.tool_calls or []:
            name, args = call.function.name, dict(call.function.arguments)
            s.tool_carry_next = True
            print(f"  [tool: {name}({args})]", flush=True)
            result = jt.call(name, args)
            print_tool(name, result)
            if not jt.dispatcher().last_result["ok"]:
                if self.speak_fn:
                    self.speak_fn(result)
                return result
            s.intent_router.record(name, args, result)
            self.messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": name, "arguments": args}}],
                }
            )
            self.messages.append({"role": "tool", "name": name, "content": result})
        return self.stream(self.messages, allow_retry=False) or ""

    def run(self) -> str:
        """The whole turn: direct answer, tool round, spoken answer."""
        answer = self.direct_answer()
        if answer is not None:
            return answer
        if self.hinted:
            answer = self.tool_round()
            if answer is not None:
                return answer
            if self.long_tool_result:
                self.messages.append({"role": "system", "content": LONG_RESULT_NOTE})
        out = None if self.stalled else self.stream(self.messages)
        if out is None:
            out = self.recover_from_stall()
        if BAD_ID.search(out):
            print("  [identity slip logged]", flush=True)
        return re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()


def ask_streaming(
    s: Session,
    user_text: str,
    vision_line: str,
    nudge: str | None = None,
    speak_fn=None,
    before_inference=None,
) -> str:
    """Answer one request: a shortcut reply, a deterministic tool answer, or the brain (tools, then speech)."""
    # A turn that used a tool carries tools into the next one, so follow-ups always have them.
    tool_carry = s.tool_carry_next
    s.tool_carry_next = False
    for reply in (
        None if nudge else voice_capability_reply(user_text),
        authority_discussion_reply(user_text),
    ):
        if reply:
            s.intent_router.last = None
            return _say(reply, speak_fn)
    if nudge:
        user_text = f"[the user has looked {nudge} for a while. Check in once, briefly.]"
    if vision_line:
        user_text = f"{user_text}\n\n[you can see: {vision_line}]"
    turn = BrainTurn(s, user_text, speak_fn, before_inference, tool_carry, nudge)
    turn.build_messages(vision_line)
    print("  ...thinking", end="", flush=True)
    return turn.run()
