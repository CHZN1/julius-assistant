"""Stop, quiet and wake controls, and the read-only state the orb's event feed reports."""

import time

from julius import log, state
from julius.audio.listening import quiet_command
from julius.runtime.session import Session


def stop_now(s: Session, source: str = "hotkey") -> None:
    """Stop generation and speech immediately (global hotkey, typed /stop)."""
    if s.cancellation.request():
        log.console(f"  [stop requested: {source}]", flush=True)
    if s.speech_player is not None:
        s.speech_player.abort()


def ui_speaking(s: Session) -> bool:
    """True for a moment after the speaker was last loud."""
    return time.monotonic() - s.ui_last_loud < 0.35


def ui_level(s: Session) -> float:
    """Current output loudness for the orb."""
    duplex, tap = s.duplex, s.ui_level_tap
    if duplex is not None and duplex.running():
        level = float(getattr(duplex, "out_level", 0.0))
    else:
        level = tap.level() if tap is not None else 0.0
    if level > 0.003:
        s.ui_last_loud = time.monotonic()
    return level


def ui_state(s: Session) -> str:
    """idle / listening / thinking / speaking / asleep / quiet, for the event feed (never changes behaviour)."""
    if ui_speaking(s):
        return "speaking"
    if s.turn_active:
        return "thinking"
    if s.recording:
        return "listening"
    if state.is_quiet():
        return "quiet"  # mic off until the wake hotkey
    if time.time() >= s.awake_until:
        return "asleep"  # ordinary sleep: wakes on "Julius"
    return "idle"


def wants_quiet(s: Session, text: str) -> bool:
    """'stop listening' / 'go quiet' mean quiet mode when the wake hotkey can be pressed;
    'go to sleep' is an ordinary sleep that wakes on 'Julius'."""
    return s.wake_listener is not None and quiet_command(text or "")


def hotkey_name(hotkey: str) -> str:
    """'<ctrl>+<alt>+j' as 'Control Option J' for speech."""
    spoken = hotkey.replace("<ctrl>", "Control").replace("<alt>", "Option")
    return spoken.replace("+", " ").title()


def sleep_line(s: Session, text: str) -> str:
    """What he says when told to stop listening."""
    if wants_quiet(s, text):
        return f"Going quiet. Press {hotkey_name(s.quiet_hotkey)} to wake me."
    return "Alright. Say Julius when you need me."


def enter_quiet(s: Session) -> None:
    """Quiet mode: the detector is fully off until the wake hotkey."""
    s.microphone_gate.set_quiet(True)
    state.set_quiet(True)
    log.console("  [quiet: detector off until the wake hotkey]", flush=True)
    log.event("quiet", on=True)


def leave_quiet(s: Session, source: str = "hotkey") -> None:
    """Back from quiet mode."""
    if not s.microphone_gate.quiet:
        return
    s.microphone_gate.set_quiet(False)
    state.set_quiet(False)
    log.console(f"  [listening again: {source}]", flush=True)
    log.event("quiet", on=False, source=source)
    if s.wake_engine is not None:
        s.wake_engine.reset()


def hotkey_wake(s: Session, source: str = "hotkey") -> None:
    """The wake hotkey (and `python -m julius.service.service wake`) wakes him in every state.

    The wake itself is the typed 'wake up' command, so it behaves exactly like saying Julius:
    awake for the session, with 'I am here.'
    """
    leave_quiet(s, source)
    if time.time() >= s.awake_until:
        log.console(f"  [awake: {source}]", flush=True)
        s.typed.append("wake up")
