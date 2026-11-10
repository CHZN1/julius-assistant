"""The state of one running assistant, passed explicitly to every step instead of module globals."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if (
    TYPE_CHECKING
):  # the component types, for readers and type checkers; nothing here is imported at run time
    from julius.agents.code_session import CodeSession
    from julius.agents.dropstage import DropStage
    from julius.agents.file_session import FileSession
    from julius.agents.utility_session import UtilitySession
    from julius.audio.cancellation import Cancellation
    from julius.audio.duplex import AecMonitor, CleanTalkOver, DuplexAudio
    from julius.audio.listening import CaptureClock
    from julius.audio.lowconf import LowConfidenceGate
    from julius.audio.mic_gate import MicrophoneGate
    from julius.audio.player import SpeechPlayer
    from julius.audio.selfecho import SelfEcho
    from julius.audio.session_record import SessionRecorder
    from julius.audio.talkover import TalkOver
    from julius.audio.tts import SpeechEngine
    from julius.brain.cloud import CloudBrain
    from julius.brain.intents import LiveIntentRouter
    from julius.brain.undo import UndoCoordinator
    from julius.brain.vision_mode import VisionMode
    from julius.costs import CostTracker
    from julius.memory.commands import MemoryAssistant
    from julius.service.hotkey import Hotkey
    from julius.ui_bridge.events import EventHub, LevelTap
    from julius.ui_bridge.uichannel import Channel
    from julius.wake.engine import WakeEngine

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


@dataclass(eq=False, repr=False)
class Session:
    """Components, conversation history, audio buffers and per-turn timings of one run.

    `boot.build_session` fills the components; the steps in `speech`, `brain_turn`, `turn` and
    `listen` read and update the fields. Tests build a Session directly and set only what a step uses.
    """

    no_devices: bool = field(
        default_factory=lambda: os.environ.get("JULIUS_VALIDATION_NO_DEVICES") == "1"
    )
    validation_output: str | None = field(
        default_factory=lambda: os.environ.get("JULIUS_VALIDATION_OUTPUT")
    )
    scripted: str | None = field(default_factory=lambda: os.environ.get("JULIUS_VALIDATION_SCRIPT"))
    validation_turns: list[dict] = field(default_factory=list)
    # Components (set by boot).
    rec: SessionRecorder | None = None  # voice-eval recordings, when one is being made
    cancellation: Cancellation | None = None
    microphone_gate: MicrophoneGate | None = None
    self_echo: SelfEcho | None = None
    eyes: VisionMode | None = None
    tts: SpeechEngine | None = None
    speech_player: SpeechPlayer | None = None
    duplex: DuplexAudio | None = None
    duplex_monitor: AecMonitor | None = None
    duplex_voice: CleanTalkOver | None = None
    talkover: TalkOver | None = None
    events_hub: EventHub | None = None
    ui_level_tap: LevelTap | None = None
    ui_channel: Channel | None = None
    drop_stage: DropStage | None = None
    cloud_brain: CloudBrain | None = None
    cloud_tracker: CostTracker | None = None
    memory: MemoryAssistant | None = None
    intent_router: LiveIntentRouter | None = None
    file_session: FileSession | None = None
    code_session: CodeSession | None = None
    utility_session: UtilitySession | None = None
    undo_coordinator: UndoCoordinator | None = None
    lowconf_gate: LowConfidenceGate | None = None
    wake_engine: WakeEngine | None = None
    wake_listener: Hotkey | None = None
    quiet_hotkey: str | None = None
    asr_model: str = ""
    asr_hint: str = ""
    wake_hint: str = ""
    audio_cfg: dict = field(default_factory=dict)
    system: str = ""
    memory_context: str = ""
    history: list[dict] = field(default_factory=list)
    # Audio buffers shared with the microphone callback.
    chunks: deque = field(default_factory=lambda: deque(maxlen=int(30 * SAMPLE_RATE / CHUNK)))
    pre_roll: deque = field(
        default_factory=lambda: deque(maxlen=max(4, round(0.5 * SAMPLE_RATE / CHUNK)))
    )
    recent_gated: deque = field(
        default_factory=lambda: deque(maxlen=int(4.0 * SAMPLE_RATE / CHUNK))
    )
    typed: deque = field(default_factory=deque)
    code_done: deque = field(default_factory=deque)
    threshold: float = 0.0045
    silence_len: float = field(default_factory=silence_length)
    capture_clock: CaptureClock | None = None
    # Conversation and wake state.
    turn: int = 0
    turn_active: bool = False
    awake_until: float = 0.0
    recording: bool = False
    held: dict | None = None
    text_only: bool = False
    noted_pending: float | None = None
    talkover_pending: bool = False
    use_raw_now: bool = False
    current_raw: Any = None  # the raw (uncleaned) audio of the utterance being handled
    ui_last_loud: float = -9.0
    # State of the turn in progress.
    tool_carry_next: bool = False
    unknown_tool_retried: bool = False
    turn_messages: tuple | None = None
    response_started: float | None = None
    first_audio_at: float | None = None
    first_token_at: float | None = None
    reasoning_seconds: float | None = None
    reasoning_release_seconds: float | None = None
    brain_first_token_seconds: float | None = None
    voice_finished_at: float | None = None
    last_asr_seconds: float | None = None
    last_asr_confidence: dict | None = None
    saved_utterances: int = 0

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
