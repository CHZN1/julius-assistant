from julius.paths import REPO_ROOT
"""Regression tests for what the first voice recording showed (2026-11-07)."""
import sys, unittest, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from julius.brain.confirm import is_yes, is_no
from julius.audio.listening import dangling, listening_control, stop_only, drop_stop_lead
from julius.agents.file_router import clean_asr, route
from julius.audio.talkover import TalkOver
from julius.audio.mic_gate import MicrophoneGate


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
        from julius.audio.speech_text import normalize_for_speech as n
        self.assertEqual(n("Sunday, November 08, 2:20 PM"), "Sunday, November eighth, two twenty P M")
        self.assertIn("March third", n("on Monday, March 3")); self.assertIn("November eighth, 2026", n("2026-11-08"))
        self.assertNotIn("oh", n("November 08").lower())

    def test_model_narrating_its_instructions_is_not_spoken(self):
        import re
        src = open(REPO_ROOT / "julius/runtime.py").read()
        rx = eval("re.compile(" + re.search(r'META_LEAK = re\.compile\((.*?)\)   #', src, re.S).group(1) + ")")
        for leak in ("The system says replies default short, but he explicitly asked for a long one, so I'll give a fuller story, kept spoken-friendly.",
                     "the user wants a long story, and the system says default replies are short, but he explicitly asked for a long one, so I'll give him a proper tale without tools."):
            self.assertTrue(rx.search(leak), leak)
        self.assertFalse(rx.search("Once there was a lighthouse keeper named Elias Varn, who had tended the light for thirty-one years."))

    def test_long_requests_are_allowed_by_the_length_prompt(self):
        from julius.audio.speech_text import BREVITY_PROMPT
        self.assertIn("long story", BREVITY_PROMPT); self.assertIn("Never mention these rules", BREVITY_PROMPT); self.assertIn("full list", BREVITY_PROMPT)
        self.assertIn("[interrupted by the user]", BREVITY_PROMPT); self.assertIn("not a complaint", BREVITY_PROMPT); self.assertIn("still give long answers in full", BREVITY_PROMPT)
        src = open(REPO_ROOT / "julius/runtime.py").read()
        self.assertIn('"[interrupted by the user]" if turn_error == \'cancelled\' else reply', src); self.assertIn("raw_backfill", src)
        self.assertIn("only for lists that came back from a tool", src); self.assertIn("Never refuse because it is long", src)


class TranscriptWindow(unittest.TestCase):
    def test_logging_window_ends_by_itself(self):
        import datetime; import julius.config as jc
        cfg = {"service": {"log_transcripts": True, "log_transcripts_until": "2026-11-14"}}
        self.assertTrue(jc.transcripts_logging(cfg, datetime.date(2026, 11, 7))); self.assertTrue(jc.transcripts_logging(cfg, datetime.date(2026, 11, 14)))
        self.assertFalse(jc.transcripts_logging(cfg, datetime.date(2026, 11, 15)))
        self.assertTrue(jc.transcripts_logging({"service": {"log_transcripts": True}}, datetime.date(2030, 1, 1)), "no end date: stays on")
        self.assertFalse(jc.transcripts_logging({"service": {"log_transcripts": False, "log_transcripts_until": "2099-01-01"}}))
        self.assertFalse(jc.transcripts_logging({"service": {"log_transcripts": True, "log_transcripts_until": "next week"}}), "unreadable date: redact")

    def test_the_log_writer_asks_every_line(self):
        from julius import log
        state = {"on": True}; lines = []
        class L:
            def info(self, s): lines.append(s)
        tee = log.Tee(open("/dev/null", "w"), L(), "info", lambda: state["on"])
        tee.write("YOU [en]: hello there\n"); state["on"] = False; tee.write("YOU [en]: secret\n")
        self.assertIn("hello there", lines[0]); self.assertIn("[transcript redacted]", lines[1])

    def test_toml_sets_a_seven_day_window(self):
        import julius.config as jc; import datetime
        s = jc.load_config()["service"]; self.assertTrue(s["log_transcripts"]); self.assertEqual(s["log_transcripts_until"], "2026-11-14")

class LiveDataRule(unittest.TestCase):
    def test_live_questions_are_recognised(self):
        import julius.brain.livedata as ld
        for q in ("Who won the NHL game last night?", "What's the price of gas in Springfield right now?", "What time does the Sharks game start tonight?", "What's the weather in Springfield?", "How much is Bitcoin?", "What's the latest news?"):
            self.assertTrue(ld.is_live_query(q), q)
        for q in ("Tell me a long story about a dragon.", "What is two plus two?", "Remember that my dog is called Rex.", "Explain how a transistor works."):
            self.assertFalse(ld.is_live_query(q), q)

    def test_figures_are_recognised(self):
        import julius.brain.livedata as ld
        for s in ("The Sharks won three to two.", "Gas is about 4.89 a gallon.", "It starts at 7:30.", "Bitcoin is around 67,000 dollars.", "It's 57 degrees.", "They won 4-2."):
            self.assertTrue(ld.states_a_figure(s), s)
        for s in ("I can't look that up right now, so I won't guess.", "Let me check on that for you.", "The Sharks are a hockey team."):
            self.assertFalse(ld.states_a_figure(s), s)

    def test_only_ungrounded_figures_about_live_questions_are_blocked(self):
        import julius.brain.livedata as ld
        self.assertTrue(ld.blocked("The Sharks won three to two.", True, False))
        self.assertFalse(ld.blocked("The Sharks won three to two.", True, True), "a tool result is behind it")
        self.assertFalse(ld.blocked("The Sharks won three to two.", False, False), "not a live question")
        self.assertFalse(ld.blocked("I can't look that up.", True, False))

    def test_tool_result_is_judged_per_turn(self):
        import julius.brain.livedata as ld
        old = [{"role": "user", "content": "a"}, {"role": "tool", "content": "x"}, {"role": "assistant", "content": "b"}]
        self.assertFalse(ld.tool_result_this_turn(old + [{"role": "user", "content": "c"}]), "an earlier turn's tool result does not ground this turn")
        self.assertTrue(ld.tool_result_this_turn(old + [{"role": "user", "content": "c"}, {"role": "tool", "content": "y"}]))

    def test_candidate_applies_the_guard_and_the_prompt_states_the_rule(self):
        src = open(REPO_ROOT / "julius/runtime.py").read()
        self.assertIn("_livedata.blocked(sent", src); self.assertIn("UNGROUNDED_LINE", src)
        from julius.audio.speech_text import TOOL_POLICY_PROMPT
        self.assertIn("LIVE FACTS", TOOL_POLICY_PROMPT); self.assertIn("never state one from memory", TOOL_POLICY_PROMPT)


if __name__ == "__main__":
    unittest.main()
