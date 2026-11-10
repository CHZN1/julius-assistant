"""The state of one running assistant, passed explicitly to every step instead of module globals."""

import os
from collections import deque
from types import SimpleNamespace

SAMPLE_RATE = 16000
CHUNK = 1024
SESSION_SECONDS = 45  # how long he stays awake after the last exchange
MIN_SPEECH = 0.4
HOLD_SECONDS = 4.0  # how long a dangling sentence waits for its rest
VOICE_SPEED = 1.05


def silence_length() -> float:
    """Seconds of silence that end an utterance (JULIUS_SILENCE_SECONDS, 0.6 to 2.5)."""
    value = float(os.environ.get("JULIUS_SILENCE_SECONDS", 1.2))
    if not 0.6 <= value <= 2.5:
        raise ValueError("JULIUS_SILENCE_SECONDS must be between 0.6 and 2.5")
    return value


class Session:
    """Components, conversation history, audio buffers and per-turn timings of one run.

    `boot.build_session` fills the components; the steps in `speech`, `brain_turn`, `turn` and
    `listen` read and update the fields. Tests build a Session directly and set only what a step uses.
    """

    def __init__(self) -> None:
        self.no_devices = os.environ.get("JULIUS_VALIDATION_NO_DEVICES") == "1"
        self.validation_output = os.environ.get("JULIUS_VALIDATION_OUTPUT")
        self.scripted = os.environ.get("JULIUS_VALIDATION_SCRIPT")
        self.validation_turns: list[dict] = []
        # Components (set by boot).
        self.rec = None  # session recorder (voice-eval recordings), or None
        self.cancellation = None
        self.microphone_gate = None
        self.self_echo = None
        self.eyes = None
        self.tts = None
        self.speech_player = None
        self.duplex = None
        self.duplex_monitor = None
        self.duplex_voice = None
        self.talkover = None
        self.events_hub = None
        self.ui_level_tap = None
        self.ui_channel = None
        self.drop_stage = None
        self.cloud_brain = None
        self.cloud_tracker = None
        self.memory = None
        self.intent_router = None
        self.file_session = None
        self.code_session = None
        self.utility_session = None
        self.undo_coordinator = None
        self.lowconf_gate = None
        self.wake_engine = None
        self.wake_listener = None
        self.quiet_hotkey = None
        self.asr_model = ""
        self.asr_hint = ""
        self.wake_hint = ""
        self.audio_cfg: dict = {}
        self.system = ""
        self.memory_context = ""
        self.history: list[dict] = []
        # Audio buffers shared with the microphone callback.
        self.chunks: deque = deque(maxlen=int(30 * SAMPLE_RATE / CHUNK))
        self.pre_roll: deque = deque(maxlen=max(4, round(0.5 * SAMPLE_RATE / CHUNK)))
        self.recent_gated: deque = deque(maxlen=int(4.0 * SAMPLE_RATE / CHUNK))
        self.typed: deque = deque()
        self.code_done: deque = deque()
        self.threshold = 0.0045
        self.silence_len = silence_length()
        self.capture_clock = None
        # Conversation and wake state.
        self.turn = 0
        self.turn_active = False
        self.awake_until = 0.0
        self.recording = False
        self.held: dict | None = None
        self.text_only = False
        self.noted_pending: float | None = None
        self.talkover_pending = False
        self.use_raw_now = False
        self.current_raw = None
        self.ui_last_loud = -9.0
        # State of the turn in progress.
        self.tool_carry_next = False
        self.unknown_tool_retried = False
        self.turn_messages: tuple | None = None
        self.response_started: float | None = None
        self.first_audio_at: float | None = None
        self.first_token_at: float | None = None
        self.reasoning_seconds: float | None = None
        self.reasoning_release_seconds: float | None = None
        self.brain_first_token_seconds: float | None = None
        self.voice_finished_at: float | None = None
        self.last_asr_seconds: float | None = None
        self.last_asr_confidence: dict | None = None
        self.saved_utterances = 0

    @property
    def cloud(self) -> bool:
        """True when the cloud brain answers (Haiku in front, the planner behind it)."""
        return self.cloud_brain is not None

    @property
    def vision_enabled(self) -> bool:
        """True when the camera and perception models are loaded."""
        return bool(getattr(self.eyes, "enabled", False))

    def rec_event(self, kind: str, **fields) -> None:
        """Write one event to the voice-eval recording, when one is being made."""
        if self.rec is not None:
            self.rec.event(kind, **fields)

    def mark(self) -> SimpleNamespace:
        """Snapshot of the timing fields, used by the per-turn report."""
        return SimpleNamespace(
            first_audio_at=self.first_audio_at, voice_finished_at=self.voice_finished_at
        )
