import unittest
from julius_selfecho import SelfEcho, overlap
from julius_microphone_gate import MicrophoneGate
class SelfEchoTests(unittest.TestCase):
    def test_his_own_words_heard_just_after_speaking_are_ignored(self):
        t=[100.];e=SelfEcho(clock=lambda:t[0]);e.note('Yeah?');t[0]+=1.2;self.assertTrue(e.is_echo('Yeah?'))
        e.note("It's 65 degrees and clear in Springfield, with a high of 69 and a low of 45.");t[0]+=.8;self.assertTrue(e.is_echo('a high of 69 and a low of 45.'))
    def test_a_new_request_or_a_late_repeat_is_not_an_echo(self):
        t=[100.];e=SelfEcho(clock=lambda:t[0]);e.note('Yeah?');t[0]+=1;self.assertFalse(e.is_echo("what's the weather in Springfield?"))
        t[0]+=10;self.assertFalse(e.is_echo('Yeah?'))
    def test_window_runs_from_the_end_of_playback_not_the_start_of_the_sentence(self):
        """Live regression (22:13:09): 'Done, the Sharks note is gone.' took longer than 5 s from the start of speak() to the end of playback, so its echo 'This note is gone.' was answered."""
        t=[100.];e=SelfEcho(clock=lambda:t[0]);e.begin();e.note('Done, the Sharks note is gone.');t[0]+=6;self.assertTrue(e.is_echo('This note is gone.'))
        e.finish();t[0]+=1.5;self.assertTrue(e.is_echo('This note is gone.'))
    def test_late_echo_of_a_long_reply_is_matched_against_every_sentence(self):
        t=[100.];e=SelfEcho(clock=lambda:t[0]);e.begin()
        for sent in ["Okay, the one you want gone is probably the Sharks one.","I need a topic to delete the right one.","Which should I forget, the Sharks or the file stuff?"]:e.note(sent);t[0]+=3
        e.finish();t[0]+=6;self.assertTrue(e.is_echo('I need a topic to delete the right one'))
        t[0]+=10;self.assertFalse(e.is_echo('I need a topic to delete the right one'))
    def test_a_short_answer_to_his_question_is_not_an_echo_late(self):
        t=[100.];e=SelfEcho(clock=lambda:t[0]);e.begin();e.note('Which should I forget, the Sharks or the file stuff?');e.finish();t[0]+=6
        self.assertFalse(e.is_echo('The Sharks.'));self.assertFalse(e.is_echo('forget the Sharks one please'))
    def test_yes_after_his_say_yes_prompt_is_an_answer_not_an_echo(self):
        """Live regression (17:37:55): 'Say yes to confirm or cancel to discard.' then the user's 'yes' ~3 s later was dropped as Julius's own voice."""
        t=[100.];e=SelfEcho(clock=lambda:t[0]);e.begin();e.note('Nothing has changed. Say yes to confirm or cancel to discard.');e.finish()
        t[0]+=.8;self.assertTrue(e.is_echo('discard.'))          # real tail echo, right at the end
        t[0]+=2.2;self.assertFalse(e.is_echo('Yes.'));self.assertFalse(e.is_echo('cancel'))
    def test_gate_reports_playback_start_and_end(self):
        calls=[];g=MicrophoneGate();g.on_begin=lambda:calls.append('b');g.on_end=lambda:calls.append('e')
        with g.output():calls.append('play')
        self.assertEqual(calls,['b','play','e'])
    def test_overlap_is_in_order(self):self.assertEqual(overlap('b a','a b c'),.5)
    def test_gate_settle_covers_the_output_device_latency(self):
        g=MicrophoneGate();g.cover_output_latency(.4);self.assertAlmostEqual(g.settle_seconds,.75)
class WakeVoiceActivityTests(unittest.TestCase):
    def test_quiet_wake_speech_reaches_whisper_but_ordinary_turns_keep_the_threshold(self):
        """Regression: 10 of 40 quiet takes (and live far-field wakes, one with detector score 0.996) were dropped by the turn VAD before Whisper ran."""
        import numpy as np, ast
        from pathlib import Path
        from julius_listening import has_voice_activity
        rng=np.random.default_rng(1);x=(rng.standard_normal(32000)*.0035).astype(np.float32)
        self.assertFalse(has_voice_activity(x,16000,.0045));self.assertTrue(has_voice_activity(x,16000,.0015))
        src=Path('julius_candidate.py').read_text();self.assertIn("min(globals().get('TH',.0045),.0015) if wake",src)
class FarFieldTests(unittest.TestCase):
    def test_stream_level_floor_is_low_enough_for_distant_speech(self):
        """Regression: the 0.0045 floor cut a distant speaker's utterance to one frame (65% missed at 2.5x the distance); 0.0015 misses 3%."""
        import julius_config
        self.assertLessEqual(julius_config.DEFAULTS["wake"]["level_floor"],.0015)
if __name__=='__main__':unittest.main()
