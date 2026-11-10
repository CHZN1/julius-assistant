"""Spoken acknowledgement for slow work (Phase F3d): if a tool call is still running after `delay` seconds and Julius has said nothing yet this turn,
he says a short "On it." once, so silence never reads as "he didn't hear me". Fast calls (most lookups) never trigger it.

    ack = Acknowledger(speak_fn, delay=1.5)
    with ack.watch(): result = slow_call()        # speaks "On it." from a timer thread if slow_call outlasts the delay
    ack.said_something()                          # call when real speech starts: cancels a pending acknowledgement and suppresses it for the rest of the turn"""

import itertools
import threading

PHRASES = ("On it.", "One moment.", "Working on it.")


class Acknowledger:
    def __init__(self, speak_fn, delay=1.5, phrases=PHRASES, enabled=True):
        self.speak_fn, self.delay, self.phrases, self.enabled = (
            speak_fn,
            delay,
            itertools.cycle(phrases),
            enabled,
        )
        self._lock = threading.Lock()
        self._timer = None
        self._done = False
        self.spoken = 0

    def _fire(self):
        with self._lock:
            if self._done or not self.enabled or self.speak_fn is None:
                return
            self._done = True
            self.spoken += 1
            phrase = next(self.phrases)
        try:
            self.speak_fn(phrase)
        except Exception:
            pass

    def said_something(self):
        with self._lock:
            self._done = True
            if self._timer:
                self._timer.cancel()

    def watch(self):
        ack = self

        class _Watch:
            def __enter__(self_):
                with ack._lock:
                    if not ack._done and ack.enabled:
                        ack._timer = threading.Timer(ack.delay, ack._fire)
                        ack._timer.daemon = True
                        ack._timer.start()

            def __exit__(self_, *exc):
                with ack._lock:
                    if ack._timer:
                        ack._timer.cancel()
                        ack._timer = None
                return False

        return _Watch()
