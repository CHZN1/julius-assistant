"""Synthetic event source for developing the UI before the service serves the real feed. Same protocol as julius_events.py, scripted conversation on a loop.

    python julius_events_synthetic.py            serve ws://127.0.0.1:8766 (8766, so it never takes the real feed's 8765)
    python julius_events_synthetic.py 9000       another port
    python julius_events_synthetic.py --once     play the script once, then keep serving idle
Script: quiet -> asleep -> idle -> listening (a question) -> thinking -> speaking (levels shaped like speech, sentence by sentence) -> idle -> a second exchange -> asleep. Ctrl+C to stop."""
import math
import random
import sys
import time

import julius_events as ev

SCRIPT = [
    ("quiet", 3.0, None), ("asleep", 3.0, None), ("idle", 1.5, None),
    ("listening", 0.4, ("you", "What's the weather in Springfield tomorrow?")), ("listening", 1.6, None),
    ("thinking", 1.8, None),
    ("speaking", 0.0, ("julius", "Tomorrow in Springfield, a high of 71 and a low of 51.")),
    ("speaking", 0.0, ("julius", "Overcast, with no rain expected.")),
    ("idle", 3.0, None),
    ("listening", 0.4, ("you", "Tell me a short story.")), ("listening", 1.0, None), ("thinking", 1.2, None),
    ("speaking", 0.0, ("julius", "A lighthouse keeper once taught the fog to sing.")),
    ("idle", 2.0, None), ("asleep", 4.0, None),
]


def speech_level(t, rng):
    """RMS-like level of speech: syllable pulses over a slow phrase envelope (hub applies the x4 gain)."""
    syl = abs(math.sin(math.pi * 4.2 * t)); phrase = 0.55 + 0.45 * abs(math.sin(math.pi * 0.7 * t))
    return max(0.0, 0.06 * syl * phrase + rng.uniform(-0.004, 0.004))


def play(hub, sleep=time.sleep, rng=None, hz=30, script=SCRIPT, turn=[0]):
    rng = rng or random.Random(7); i = 0
    while i < len(script):
        state, hold, line = script[i]; hub.set_state(state)
        if line:
            if line[0] == "you": turn[0] += 1
            hub.say(line[0], line[1], turn[0])
        if state == "speaking":
            words = len(line[1].split()); dur = 0.32 * words; n = int(dur * hz)
            for k in range(n): hub.set_level(speech_level(k / hz, rng)); sleep(1 / hz)
            hub.set_level(0.0)
            nxt = script[i + 1] if i + 1 < len(script) else None
            if nxt and nxt[0] == "speaking": sleep(0.25)   # a pause between sentences
        sleep(hold); i += 1


def main(argv):
    port = next((int(a) for a in argv if a.isdigit()), 8766); once = "--once" in argv
    hub = ev.EventHub(); srv = ev.EventServer(hub, port=port).start(); print(f"synthetic feed on ws://127.0.0.1:{srv.port} (Ctrl+C to stop)", flush=True)
    try:
        while True:
            play(hub)
            if once: hub.set_state("idle"); time.sleep(10 ** 6)
    except KeyboardInterrupt: pass
    srv.stop(); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
