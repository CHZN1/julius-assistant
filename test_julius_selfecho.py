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
