"""Demo mode's voice: played silently in real time so the orb speaks in sync, saved for the video's soundtrack, and "Narrate:" said verbatim."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from julius.runtime import speech, turn
from julius.ui_bridge import events


def session(**kw):
    base = dict(
        demo=True,
        no_devices=True,
        cancellation=None,
        ui_level_tap=events.LevelTap(),
        response_started=None,
        first_audio_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class DemoPlayback(unittest.TestCase):
    def test_the_voice_meters_the_orb_in_real_time_and_is_saved(self):
        rate = 24000
        chunks = [
            (np.full(rate // 10, 0.5, np.float32), rate),
            (np.zeros(rate // 10, np.float32), rate),
        ]
        seen = []
        s = session()
        s.ui_level_tap.out = lambda a, r: seen.append(float(np.sqrt(np.mean(a * a))))
        with (
            tempfile.TemporaryDirectory() as d,
            mock.patch.dict(os.environ, {"JULIUS_DEMO_SPEECH_DIR": d}),
        ):
            t = time.time()
            speech._play_demo(s, iter(chunks), "Hello there.")
            took = time.time() - t
            rows = [json.loads(x) for x in (Path(d) / "speech.jsonl").read_text().splitlines()]
            self.assertTrue((Path(d) / rows[0]["file"]).exists())
        self.assertGreaterEqual(
            took, 0.18
        )  # 0.2 s of audio takes 0.2 s: the orb sees it as it would be heard
        self.assertEqual((rows[0]["text"], rows[0]["dur"]), ("Hello there.", 0.2))
        self.assertGreater(seen[0], 0.4)  # loud first, then silent
        self.assertEqual(seen[-1], 0.0)

    def test_play_uses_the_demo_path_only_in_demo_mode_without_devices(self):
        with (
            mock.patch.object(speech, "_play_demo") as demo,
            mock.patch.object(speech, "_play_chunks") as chunks,
        ):
            speech._play(session(), iter([]), "x")
            speech._play(session(demo=False, tts=SimpleNamespace(backend="kokoro")), iter([]), "x")
        demo.assert_called_once()
        chunks.assert_called_once()


class Narrate(unittest.TestCase):
    def test_narrate_is_said_verbatim_in_demo_mode_only(self):
        self.assertEqual(
            turn._command_reply(SimpleNamespace(demo=True), "Narrate: I'm Julius."), "I'm Julius."
        )
        with (
            mock.patch.object(turn, "authority_discussion_reply", return_value=None),
            mock.patch.object(turn, "incomplete_fragment", return_value=False),
            mock.patch.object(turn, "handle_panel", return_value="routed") as hp,
        ):
            self.assertEqual(
                turn._command_reply(SimpleNamespace(demo=False), "Narrate: I'm Julius."), "routed"
            )
        hp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
