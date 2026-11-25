"""Start-up: build every component of a Session, in the order the old script did."""

import atexit
import contextlib
import json
import os
import signal
from pathlib import Path
from typing import Any

import sounddevice as sd

import julius.agents.files as jf
import julius.brain.tools as jt
import julius.config as config
import julius.memory.sessions as jm
from julius import identity, log, profile, state
from julius.agents.code_session import CodeSession
from julius.agents.file_session import FileSession
from julius.agents.utility_session import UtilitySession
from julius.audio import lowconf, session_record
from julius.audio.cancellation import Cancellation
from julius.audio.listening import CaptureClock
from julius.audio.mic_gate import MicrophoneGate
from julius.audio.selfecho import SelfEcho
from julius.audio.tts import SpeechEngine
from julius.brain.apple import AppleProvider
from julius.brain.backend import LLM_BACKEND, _offline_chat, prepare_backend
from julius.brain.intents import LiveIntentRouter
from julius.brain.vision_mode import create_vision
from julius.memory import commands, store
from julius.runtime import capture, controls, test_run
from julius.runtime.prompt import build_system
from julius.runtime.session import Session

ASR_MODELS = {
    "small.en": "mlx-community/whisper-small.en-mlx",
    "distil-large-v3": "mlx-community/distil-whisper-large-v3",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}
# Memory lines about the assistant failing or the user's mood are not carried into the prompt.
UNWANTED_MEMORY = (
    "frustrat",
    "no progress",
    "lack of progress",
    "unable to",
    "can't ",
    "cannot ",
    "failed",
    "impatient",
    "confrontational",
    "issue with",
    "struggling",
    "not completed",
    "resolve the issue",
)


def asr_model_for(name: str) -> str:
    """The mlx-whisper repository for a short model name (any other value is used as a repo id)."""
    return ASR_MODELS.get(name, name)


def setup_basics(s: Session) -> None:
    """Cancellation, microphone gate, self-echo filter, recording and the memory folder."""
    if s.scripted and (test_root := test_run.isolate()):
        log.console(f"  [test run: memory, conversations and costs go to {test_root}]", flush=True)
    s.cancellation = Cancellation()
    s.microphone_gate = MicrophoneGate()
    s.self_echo = SelfEcho()
    s.microphone_gate.on_begin = s.self_echo.begin
    s.microphone_gate.on_end = (
        s.self_echo.finish
    )  # his own voice is judged from the END of playback
    # None unless JULIUS_SESSION_RECORD_DIR is set (voice eval recordings, local only).
    s.rec = session_record.from_env({"profile": os.environ.get("JULIUS_PROFILE")})
    if s.rec is not None:
        atexit.register(s.rec.close)
    if os.environ.get("JULIUS_MEMORY_DIR"):
        jm.MEM = Path(os.environ["JULIUS_MEMORY_DIR"])
        jm.SESS = jm.MEM / "sessions"
    if s.validation_output:
        atexit.register(
            lambda: Path(s.validation_output).write_text(json.dumps(s.validation_turns, indent=2))
        )
    s.asr_model = asr_model_for(os.environ.get("JULIUS_ASR_MODEL", "small.en"))
    s.asr_hint = config.asr_initial_prompt()  # vocabulary hint from [asr] and [profile]
    s.wake_hint = config.asr_initial_prompt(wake=True)
    s.intent_router = LiveIntentRouter()
    if profile.needs_setup():
        log.console(
            "  [first run: no name or home city set. Run `python -m julius.profile setup`, "
            f"or just tell {identity.name()} your city when you ask for the weather]",
            flush=True,
        )


def setup_vision(s: Session) -> None:
    """Camera and perception models, when enabled."""
    log.console("initializing optional vision...", flush=True)
    s.eyes = create_vision(
        show_window=False,
        target_fps=float(os.environ.get("JULIUS_VISION_FPS", "2")),
        emotion_interval=float(os.environ.get("JULIUS_EMOTION_INTERVAL", "1")),
        camera_source=os.environ.get("JULIUS_CAMERA_SOURCE", 0),
        allowed_modules=["detection", "recognition"],
    ).start()
    if not s.eyes.enabled:
        log.console("  [vision disabled — camera and perception models not loaded]", flush=True)
    if os.environ.get("JULIUS_MLX_BRAIN_PREPARE") == "1":
        log.console("preparing native reasoning; weights will unload before voice...", flush=True)
        prepare_backend()


def duplex_wanted(s: Session) -> bool:
    """One stream for microphone and speakers with echo cancellation (audio.duplex or JULIUS_DUPLEX=1)."""
    if s.no_devices:
        return False
    if os.environ.get("JULIUS_DUPLEX") == "1":
        return True
    return bool(s.audio_cfg.get("duplex")) and os.environ.get("JULIUS_DUPLEX") != "0"


def setup_duplex(s: Session) -> None:
    """Echo-cancelled duplex audio; falls back to the microphone gate when it cannot start."""
    try:
        from julius.audio.aec import EchoCanceller
        from julius.audio.duplex import AecMonitor, CleanTalkOver, DuplexAudio

        s.duplex_monitor = AecMonitor(
            on_fallback=lambda why: capture.duplex_fallback(s, why),
            fail_seconds=float(s.audio_cfg.get("fallback_seconds", 3.0)),
        )
        s.duplex = DuplexAudio(
            sd,
            lambda *a: capture.duplex_block(s, *a),
            rate=int(s.audio_cfg.get("duplex_rate", 48000)),
            canceller=EchoCanceller(lag=0.0),
            monitor=s.duplex_monitor,
        )
        s.duplex_voice = CleanTalkOver(s.duplex_monitor)
        s.microphone_gate.bypass = True
        atexit.register(s.duplex.stop)
        s.rec_event("duplex", on=True, rate=s.duplex.rate)
        log.console(
            f"  [duplex audio on: echo cancelled, mic gate off, speaking over {identity.name()} stops him "
            f"(falls back to the gate after {s.duplex_monitor.fail_seconds:.0f} s of leaking echo)]",
            flush=True,
        )
    except Exception as exc:
        s.duplex = None
        s.microphone_gate.bypass = False
        log.console(
            f"  [duplex audio unavailable ({type(exc).__name__}: {exc}); using the microphone gate]",
            flush=True,
        )


def setup_talkover(s: Session) -> None:
    """Detect the user's voice over Julius's playback and feed it the speaker's output."""
    from julius.audio.talkover import TalkOver

    s.talkover = TalkOver()
    recorder_tap = getattr(s.speech_player, "tap", None)

    def tap_both(audio: Any, rate: int) -> None:
        s.talkover.out(audio, rate)
        if recorder_tap is not None:
            recorder_tap(audio, rate)

    s.speech_player.tap = tap_both


def setup_voice(s: Session) -> None:
    """Speech engine, optional duplex audio and the persistent output stream of the MLX voice."""
    log.console("loading speech engine...", flush=True)
    s.tts = SpeechEngine()
    s.tts.cancel_event = s.cancellation.event
    s.audio_cfg = config.load_config().get("audio") or {}
    if duplex_wanted(s) and s.tts.backend == "qwen-mlx":
        setup_duplex(s)
    if s.tts.backend != "qwen-mlx":
        return
    from julius.audio.player import SpeechPlayer

    s.speech_player = SpeechPlayer(
        s.duplex.shim() if s.duplex is not None else sd,
        prefill_chunks=int(os.environ.get("JULIUS_AUDIO_PREFILL_CHUNKS", "1")),
        latency=float(os.environ.get("JULIUS_AUDIO_LATENCY", "0.4")),
    )
    s.microphone_gate.cover_output_latency(s.speech_player.latency)
    if s.rec is not None and s.duplex is None:
        s.speech_player.tap = s.rec.out  # duplex recordings take the reference from the callback
    barge_in = config.load_config().get("barge_in") or {}
    if not s.no_devices and barge_in.get("voice", True):
        setup_talkover(s)


def setup_events_feed(s: Session) -> None:
    """The read-only event feed the orb listens to (ws://127.0.0.1)."""
    try:
        from julius.ui_bridge import events

        if (
            s.demo and s.no_devices
        ):  # demo mode plays the voice silently in real time (speech._play_demo) and meters it here
            s.ui_level_tap = events.LevelTap()
        elif s.duplex is None and s.speech_player is not None:
            s.ui_level_tap = events.LevelTap()
            previous = getattr(s.speech_player, "tap", None)

            def tap_level(audio: Any, rate: int) -> None:
                s.ui_level_tap.out(audio, rate)
                if previous is not None:
                    previous(audio, rate)

            s.speech_player.tap = tap_level
        s.events_hub, server = events.start_from_config(
            config.load_config().get("events"),
            lambda: controls.ui_state(s),
            lambda: controls.ui_level(s),
        )
        if server:
            log.console(
                f"  [ui event feed: ws://127.0.0.1:{server.port} (read-only, this machine only)]",
                flush=True,
            )
        setup_panel(s)
    except Exception as exc:
        s.events_hub = None
        log.console(f"  [ui event feed unavailable: {type(exc).__name__}: {exc}]", flush=True)


def setup_panel(s: Session) -> None:
    """The side panel: card data on the event feed, images from a small local file server. Research results go to it."""
    from julius.agents import research
    from julius.agents.research import HOME
    from julius.ui_bridge import panel_emit

    started = panel_emit.start_panel(
        s.events_hub, config.load_config().get("ui") or {}, HOME / "panel"
    )
    if started is None:
        return
    s.panel_hub, s.panel_files, server = started
    research.set_panel_sink(panel_emit.make_sink(s.panel_hub, s.panel_files, lambda: s.turn))
    from julius.agents import file_common

    file_common.set_panel_sink(panel_emit.make_file_sink(s.panel_hub, lambda: s.turn))
    from julius.ui_bridge import charts

    charts.set_panel(s.panel_hub, s.panel_files, lambda: s.turn)
    if (config.load_config().get("vision") or {}).get("camera", True) is False:
        s.panel_hub.set_camera_status(
            False, "The camera is switched off in my settings ([vision] camera).", "settings"
        )
    from julius.art import source as art_source
    from julius.music import tool as music_tool

    music_tool.set_drop_source(lambda: s.drop_stage.pending() if s.drop_stage else None)
    art_source.set_drop_source(lambda: s.drop_stage.pending() if s.drop_stage else None)
    log.console(
        f"  [side panel: images on http://127.0.0.1:{server.port} (this machine only)]", flush=True
    )


def setup_stop_hotkey(s: Session) -> None:
    """The global stop hotkey."""
    from julius.service import hotkey

    barge_in = config.load_config().get("barge_in") or {}
    hotkey.start(
        lambda source="hotkey": controls.stop_now(s, source),
        barge_in.get("hotkey", "<ctrl>+<alt>+s"),
    )


def setup_wake_hotkey(s: Session) -> None:
    """The quiet-mode wake hotkey and the wake signal."""
    from julius.service import hotkey

    s.quiet_hotkey = (config.load_config().get("service") or {}).get(
        "wake_hotkey", "<ctrl>+<alt>+j"
    )
    if state.is_quiet():
        s.microphone_gate.set_quiet(True)
        log.console("  [starting in quiet mode: press the wake hotkey]", flush=True)
    s.wake_listener = hotkey.start(lambda: controls.hotkey_wake(s, "hotkey"), s.quiet_hotkey)
    with contextlib.suppress(Exception):
        signal.signal(signal.SIGUSR1, lambda *a: controls.hotkey_wake(s, "signal"))


def setup_cloud_brain(s: Session) -> None:
    """Claude Haiku answers every turn; the planner (Sonnet) is reached through escalate_to_planner."""
    if LLM_BACKEND != "cloud":
        return
    from julius.brain import cloud

    s.cloud_brain = cloud.get_brain(offline=_offline_chat)
    s.cloud_tracker = cloud.get_tracker()
    models = s.cloud_brain.config["models"]
    log.console(
        f"  [cloud brain: {models['haiku']} front end, {models['sonnet']} planner; "
        f"API key {config.describe_key()}; daily cap ${s.cloud_tracker.cap_usd:.2f}, "
        f"spent ${s.cloud_tracker.today_total():.4f}; "
        f"offline model {s.cloud_brain.config['offline']['model']}]",
        flush=True,
    )


def import_old_facts(memory_store: Any) -> None:
    """One-time import of the old facts.json; the file is kept as facts.migrated.json."""
    try:
        old = jm.MEM / "facts.json"
        if old.exists() and old.read_text().strip():
            for fact in json.loads(old.read_text()).get("facts", []):
                memory_store.add(fact["text"], "fact", "auto", "")
            old.rename(jm.MEM / "facts.migrated.json")
    except Exception as exc:
        log.console(f"  [memory migration skipped: {type(exc).__name__}]", flush=True)


def setup_memory(s: Session) -> None:
    """Long-term memory, the remembered-notes context and the finished system prompt."""
    memory_store = store.MemoryStore(jm.MEM / "memory.sqlite3")
    import_old_facts(memory_store)
    s.memory = commands.MemoryAssistant(
        memory_store, jm.SESS, show=lambda text: log.console(text, flush=True)
    )
    jt.memory_assistant = (
        s.memory
    )  # backup path: tools can remember/forget/list when a phrasing slips past
    context = jm.build_context()
    if context:
        lines = [
            x for x in context.splitlines() if not any(b in x.lower() for b in UNWANTED_MEMORY)
        ]
        context = "\n".join(lines)
    s.memory_context = context
    if context.strip():
        log.console(f"  [memory: {len(context.splitlines())} lines]")
    s.system = build_system(s.vision_enabled, LLM_BACKEND == "cloud", context)
    s.history = [{"role": "system", "content": s.system}]
    setup_conversations(s)


def setup_conversations(s: Session) -> None:
    """Conversations (julius/conversations): each turn is kept in the current conversation; a recent one carries on after a restart."""
    if not (config.load_config().get("conversations") or {}).get("enabled", True):
        return
    try:
        from julius.conversations.manager import Conversations
        from julius.conversations.store import ConversationStore

        s.conversations = Conversations(ConversationStore())
        jt.conversations_manager = s.conversations
        hub = getattr(s, "panel_hub", None)
        if (
            hub is not None
        ):  # the sidebar feed, and each card kept with the conversation it was made in
            hub.conv_fn = s.conversations.ensure
            s.conversations.on_change = hub.conversations
            s.conversations.changed()
        title = s.conversations.resume(s.history)
        if title:
            log.console(
                f"  [conversation: carrying on {title!r}, {len(s.history) - 1} messages]",
                flush=True,
            )
    except Exception as exc:
        s.conversations = None
        log.console(f"  [conversations unavailable: {type(exc).__name__}: {exc}]", flush=True)


def setup_agents(s: Session) -> None:
    """File, coding and planner sessions, cross-agent undo and the cloud orchestrator."""
    s.file_session = FileSession(jf.agent())
    try:
        trash_note = jf.agent().trash_notice()
        if trash_note:
            log.console(f"  [trash:{trash_note}]", flush=True)
    except Exception:
        pass
    s.code_session = CodeSession(
        config=config.load_config().get("code"),
        tracker=s.cloud_tracker,
        api_key=config.api_key,
        on_done=s.code_done.append,
    )
    jt.file_session = (
        s.file_session
    )  # backup path: tools can hand a file or coding request to these
    jt.code_session = s.code_session
    from julius.runtime.undo import build_undo_coordinator

    try:
        s.undo_coordinator = build_undo_coordinator(s)
    except Exception as exc:
        s.undo_coordinator = None
        log.console(f"  [cross-agent undo unavailable: {type(exc).__name__}: {exc}]", flush=True)
    if s.cloud:
        from julius.brain import orchestrator

        orchestrator.configure(
            file_session=s.file_session, turn=lambda: s.turn, code_session=s.code_session
        )
    s.utility_session = UtilitySession(
        jt.planner(),
        os.environ.get("JULIUS_TIMEZONE", "America/Los_Angeles"),
        AppleProvider(jt.planner()),
    )
    s.lowconf_gate = lowconf.LowConfidenceGate()


def build_session() -> Session:
    """Create the Session and bring every component up (models load here)."""
    s = Session()
    setup_basics(s)
    setup_vision(s)
    setup_voice(s)
    if not s.no_devices:
        setup_stop_hotkey(s)
        setup_events_feed(s)
        setup_wake_hotkey(s)
    elif s.demo:
        setup_events_feed(s)  # demo mode: the orb is the only input
    setup_cloud_brain(s)
    setup_memory(s)
    setup_agents(s)
    s.capture_clock = CaptureClock(max_gap_seconds=max(2.0, 2 * s.silence_len))
    return s
