"""Test helpers: a Session with mock components, and runners that patch module-level collaborators."""

import contextlib
import io
from unittest.mock import Mock, patch

import julius.brain.tools as jt
from julius.audio.cancellation import Cancellation
from julius.audio.mic_gate import MicrophoneGate
from julius.audio.selfecho import SelfEcho
from julius.brain.backend import llm_chat
from julius.brain.intents import LiveIntentRouter
from julius.runtime import brain_turn, turn
from julius.runtime.session import Session


def make_session(**attributes) -> Session:
    """A Session whose devices are mocks and whose conversation starts empty."""
    s = Session()
    s.cancellation = Cancellation()
    s.microphone_gate = MicrophoneGate()
    s.self_echo = SelfEcho()
    s.eyes = Mock(enabled=False)
    s.eyes.snapshot.return_value = {}
    s.eyes.describe.return_value = ""
    s.tts = Mock(backend="qwen-mlx", worker_metrics={})
    s.intent_router = LiveIntentRouter()
    s.history = [{"role": "system", "content": "system prompt"}]
    for name, value in attributes.items():
        setattr(s, name, value)
    return s


@contextlib.contextmanager
def quiet():
    """Swallow what the runtime prints."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


@contextlib.contextmanager
def patched(**targets):
    """Patch `module.name` attributes of the runtime package: patched(**{"turn.speak": mock})."""
    with contextlib.ExitStack() as stack:
        for dotted, value in targets.items():
            module, name = dotted.split(".")
            stack.enter_context(patch(f"julius.runtime.{module}.{name}", value))
        yield


class TurnHarness:
    """Runs `ask_streaming` against a mock Session with a scripted `llm_chat`."""

    def __init__(self, cloud: bool = False) -> None:
        self.session = make_session()
        self.session.cloud_brain = object() if cloud else None
        self.llm_chat = llm_chat
        self.jt = jt

    def ask(self, prompt: str, vision: str = "", **kwargs) -> str:
        with patched(**{"brain_turn.llm_chat": self.llm_chat, "brain_turn.jt": self.jt}):
            return brain_turn.ask_streaming(self.session, prompt, vision, **kwargs)


class ProcessHarness:
    """Runs `process` with mock collaborators; `mocks` holds the patched module attributes."""

    def __init__(self, validation_output: str | None = None, **session_attributes) -> None:
        self.session = make_session(
            tts=Mock(backend="qwen-clone", worker_metrics={}),
            validation_output=validation_output,
            **session_attributes,
        )
        self.mocks = {
            "turn.ask_streaming": Mock(return_value="A response."),
            "turn.speak": Mock(),
            "turn.speak_sentence": Mock(),
            "turn.close_backend": Mock(),
            "turn.keep_brain_loaded": Mock(return_value=False),
            "turn.handle_files": Mock(return_value=None),
            "turn.handle_memory": Mock(return_value=None),
            "turn.handle_code": Mock(return_value=None),
            "turn.memory_autocapture": Mock(),
            "turn.jm": Mock(),
            "turn.jf": Mock(),
            "turn.jt": Mock(),
        }

    def __getitem__(self, dotted: str) -> Mock:
        return self.mocks[dotted]

    def process(self, text: str, lang: str = "typed") -> None:
        with patched(**self.mocks), quiet():
            turn.process(self.session, text, lang)
