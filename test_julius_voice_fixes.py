"""Regression tests for what the first voice recording showed (2026-11-07)."""
import sys, unittest, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from julius_confirm import is_yes, is_no
from julius_listening import dangling, listening_control, stop_only, drop_stop_lead
from julius_router import clean_asr, route
from julius_talkover import TalkOver
from julius_microphone_gate import MicrophoneGate


class Confirmations(unittest.TestCase):
    def test_natural_yes(self):
        for t in ["Sure, go for it.", "yeah", "Yeah, do it please", "okay", "Absolutely.", "sounds good", "yes please"]: self.assertTrue(is_yes(t), t)

    def test_natural_no(self):
        for t in ["Nah.", "No thanks", "nope", "never mind", "leave it", "Don't", "cancel"]: self.assertTrue(is_no(t), t)

    def test_mixed_answers_are_not_bare(self):
        for t in ["yes but only the old ones", "no wait I meant the other file", "what time is it", "undo that"]: self.assertFalse(is_yes(t) or is_no(t), t)

    def test_router_uses_them_when_pending(self):
        self.assertEqual(route("Sure, go for it.", Path("/tmp"), pending=True)[0], "approve")
        self.assertEqual(route("Nah.", Path("/tmp"), pending=True)[0], "cancel")
        self.assertNotIn(route("Nah.", Path("/tmp"), pending=False), [("cancel", None)])


class AsrGlue(unittest.TestCase):
    def test_lowercase_glued_trash_with_filename(self):
        self.assertEqual(clean_asr("Trashnotes.txt in Sandbox."), "Trash notes.txt in Sandbox.")
        self.assertIn(route(clean_asr("Trashnotes.txt in Sandbox."), Path("/tmp/x"))[0], ("trash", "clarify"))

    def test_other_words_untouched(self):
        self.assertEqual(clean_asr("Trashcan stuff"), "Trashcan stuff")


class Sleep(unittest.TestCase):
    def test_courtesies_around_sleep(self):
        for t in ["Thanks, go to sleep.", "Go to sleep.", "Julius, go to sleep now", "okay go to sleep please"]: self.assertEqual(listening_control(t), "sleep", t)
        self.assertIsNone(listening_control("thanks"))
        self.assertIsNone(listening_control("what does sleep do"))


class Fragments(unittest.TestCase):
    def test_dangling(self):
        for t in ["what time is it in?", "Tell me.", "show me the", "what is the weather in"]: self.assertTrue(dangling(t), t)
        for t in ["What about tomorrow?", "Thanks.", "a fun fact about octopuses", "what is that used for"]: self.assertFalse(dangling(t), t)


class StopWords(unittest.TestCase):
    def test_stop(self):
        for t in ["Stop.", "Okay, stop!", "be quiet", "hold on"]: self.assertTrue(stop_only(t), t)
        self.assertFalse(stop_only("stop the music in the playlist"))
        self.assertEqual(drop_stop_lead("Stop. What's the weather in Springfield?"), "What's the weather in Springfield?")
        self.assertEqual(drop_stop_lead("stop it"), "stop it")


class TalkOverDetector(unittest.TestCase):
    def feed(self, det, mic_level, out_level, seconds):
        fired = False
        for _ in range(int(seconds / 0.1)): fired = det.update(mic_level, out_level, 0.1) or fired
        return fired

    def test_echo_alone_never_triggers(self):
        d = TalkOver(); self.assertFalse(self.feed(d, 0.005, 0.15, 10))

    def test_speaking_over_playback_triggers(self):
        d = TalkOver(); self.feed(d, 0.004, 0.15, 3); self.assertTrue(self.feed(d, 0.03, 0.15, 0.6))

    def test_speaking_in_a_gap_between_sentences_triggers(self):
        d = TalkOver(); self.assertTrue(self.feed(d, 0.03, 0.0, 0.6))

    def test_a_click_does_not(self):
        d = TalkOver(); self.assertFalse(self.feed(d, 0.05, 0.1, 0.1))

    def test_loud_speech_in_a_gap_does_not_corrupt_the_echo_estimate(self):
        d = TalkOver(); self.feed(d, 0.05, 0.0, 2); self.assertLessEqual(d.ratio, 0.10)

    def test_mic_blocks_are_pooled(self):
        d = TalkOver(); fired = False
        for _ in range(12): fired = d.mic(np.full(1024, 0.04, dtype=np.float32)) or fired
        self.assertTrue(fired)


class GateForceOpen(unittest.TestCase):
    def test_force_open_listens_at_once_and_clears_on_next_playback(self):
        g = MicrophoneGate()
        with g.output():
            self.assertFalse(g.accepting()); g.force_open(); self.assertTrue(g.accepting())
        with g.output(): self.assertFalse(g.accepting())


class SpokenDatesAndLeaks(unittest.TestCase):
    def test_dates_are_said_as_ordinals(self):
        from julius_speech_text import normalize_for_speech as n
        self.assertEqual(n("Sunday, November 08, 2:20 PM"), "Sunday, November eighth, two twenty P M")
        self.assertIn("March third", n("on Monday, March 3")); self.assertIn("November eighth, 2026", n("2026-11-08"))
        self.assertNotIn("oh", n("November 08").lower())

    def test_model_narrating_its_instructions_is_not_spoken(self):
        import re
        src = open(Path(__file__).resolve().parent / "julius_candidate.py").read()
        rx = eval("re.compile(" + re.search(r'META_LEAK = re\.compile\((.*?)\)   #', src, re.S).group(1) + ")")
        for leak in ("The system says replies default short, but he explicitly asked for a long one, so I'll give a fuller story, kept spoken-friendly.",
                     "the user wants a long story, and the system says default replies are short, but he explicitly asked for a long one, so I'll give him a proper tale without tools."):
            self.assertTrue(rx.search(leak), leak)
        self.assertFalse(rx.search("Once there was a lighthouse keeper named Elias Varn, who had tended the light for thirty-one years."))

    def test_long_requests_are_allowed_by_the_length_prompt(self):
        from julius_speech_text import BREVITY_PROMPT
        self.assertIn("long story", BREVITY_PROMPT); self.assertIn("Never mention these rules", BREVITY_PROMPT); self.assertIn("full list", BREVITY_PROMPT)
        src = open(Path(__file__).resolve().parent / "julius_candidate.py").read()
        self.assertIn("only for lists that came back from a tool", src); self.assertIn("Never refuse because it is long", src)


if __name__ == "__main__":
    unittest.main()


class StatusAfterFinish(unittest.TestCase):
    def test_status_question_after_the_task_finished_is_answered_by_the_coding_session(self):
        import julius_code_session as cs
        src = open(cs.__file__).read()
        self.assertIn("self.latest().status in FINISHED", src)
        self.assertTrue(cs.LOOSE_STATUS.match("what's the status"))


class SleepVersusQuiet(unittest.TestCase):
    def test_go_to_sleep_is_ordinary_sleep(self):
        from julius_listening import quiet_command
        for t in ["Go to sleep.", "Thanks, go to sleep.", "go back to sleep", "time to sleep"]:
            self.assertEqual(listening_control(t), "sleep", t); self.assertFalse(quiet_command(t), t)

    def test_only_stop_listening_and_go_quiet_turn_the_microphone_off(self):
        from julius_listening import quiet_command
        for t in ["Stop listening.", "Go quiet.", "okay go quiet please", "Julius, stop listening"]:
            self.assertEqual(listening_control(t), "sleep", t); self.assertTrue(quiet_command(t), t)
        self.assertIsNone(listening_control("Stop listening to that song."))

    def test_candidate_uses_quiet_command_only(self):
        src = open(Path(__file__).resolve().parent / "julius_candidate.py").read()
        self.assertIn("quiet_command(t or '')", src); self.assertNotIn("'go to sleep','go back to sleep'}", src)

    def test_scenario_expectation_accepts_the_ordinary_sleep_line(self):
        import json as _j
        sc = _j.loads((Path(__file__).resolve().parent / "evals" / "voice_scenarios.json").read_text())["scenarios"]
        sleep_turns = [t for s in sc for t in s["turns"] if t["expect"].get("kind") == "sleep"]
        self.assertTrue(sleep_turns)
        for t in sleep_turns: self.assertRegex("Alright. Say Julius when you need me.", re.compile("|".join(t["expect"]["must"]), re.I))


class StartupWiring(unittest.TestCase):
    def test_talkover_tap_does_not_assume_a_recorder(self):
        """The 11-07 crash loop: speech_player.tap only exists while recording, and the service does not record."""
        src = (Path(__file__).resolve().parent / "julius_candidate.py").read_text()
        self.assertNotIn("_tap_rec = speech_player.tap", src)
        self.assertIn('getattr(speech_player, "tap", None)', src)
