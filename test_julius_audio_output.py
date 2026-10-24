import unittest
from unittest.mock import Mock
from julius_audio_output import ready_output

class OutputRecoveryTests(unittest.TestCase):
    def test_start_failure_retries_before_any_audio(self):
        failed=Mock();failed.__enter__=Mock(side_effect=RuntimeError('device waking'))
        good=Mock();good.__enter__=Mock(return_value=good);good.__exit__=Mock()
        factory=Mock(side_effect=[failed,good]);sleep=Mock()
        with ready_output(factory,RuntimeError,sleep) as output:output.write('audio')
        failed.close.assert_called_once();failed.write.assert_not_called()
        good.write.assert_called_once_with('audio');sleep.assert_called_once_with(.25)
    def test_midplayback_failure_never_replays(self):
        stream=Mock();stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock()
        factory=Mock(return_value=stream)
        with self.assertRaises(RuntimeError):
            with ready_output(factory,RuntimeError):raise RuntimeError('midplayback')
        factory.assert_called_once();stream.__exit__.assert_called_once()
    def test_persistent_failure_is_bounded(self):
        factory=Mock(side_effect=RuntimeError('offline'));sleep=Mock()
        with self.assertRaises(RuntimeError):
            with ready_output(factory,RuntimeError,sleep):pass
        self.assertEqual(factory.call_count,4);self.assertEqual(sleep.call_count,3)
