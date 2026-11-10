"""Top-level run: boot, calibrate, listen, and report the validation exit code."""

from julius import log
from julius.brain.backend import LLM, LLM_BACKEND
from julius.runtime import asr, boot, capture, inputs
from julius.runtime.listen import Listener
from julius.runtime.session import Session


def exit_code(s: Session) -> int:
    """1 when a validation run recorded a failed turn, else 0."""
    return int(bool(s.validation_output and any(row.get("error") for row in s.validation_turns)))


def main() -> int:
    """Run the assistant until it is stopped."""
    s = boot.build_session()
    inputs.start_terminal(s)
    inputs.start_ui_channel(s)
    capture.calibrate(s)
    asr.setup_wake(s)
    log.console(
        f'\njulius ready · {LLM_BACKEND}:{LLM} · say "Julius" to wake or just type · Ctrl+C to quit\n'
    )
    Listener(s).run()
    return exit_code(s)
