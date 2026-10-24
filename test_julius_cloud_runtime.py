"""Runtime wiring for the cloud brain: voice stays loaded, escalation hints reach tool turns."""
import ast, os, re, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from julius_tts import SpeechEngine

def candidate_constant(name):
    for n in ast.parse(Path('julius_candidate.py').read_text()).body:
        if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets):
            ns={'re':re};exec(compile(ast.Module(body=[n],type_ignores=[]),'<c>','exec'),ns);return ns[name]
class VoiceStaysLoadedTests(unittest.TestCase):
    def engine(self):
        e=SpeechEngine.__new__(SpeechEngine);e.backend='qwen-clone';e._isolated_worker=True;e.worker=Mock();e.cancel_event=None;return e
    def test_keep_voice_makes_suspend_a_no_op(self):
        e=self.engine()
        with patch.dict(os.environ,{'JULIUS_KEEP_VOICE':'1'}):e.suspend()
        e.worker.stdin.write.assert_not_called();e.worker.terminate.assert_not_called();e.worker.poll.assert_not_called()
    def test_without_the_flag_suspend_still_releases_the_voice(self):
        e=self.engine();e.close=Mock()
        with patch.dict(os.environ,{'JULIUS_KEEP_VOICE':'0'}):e.suspend()
        self.assertTrue(e.close.called or e.worker.mock_calls)
    def test_cloud_profile_keeps_the_voice_by_default(self):
        launcher=Path('run_julius.sh').read_text()
        self.assertIn('cloud) PROFILE_BACKEND=cloud',launcher);self.assertIn('JULIUS_KEEP_VOICE',launcher);self.assertIn('${JULIUS_PROFILE:-cloud}',launcher)
class EscalationHintTests(unittest.TestCase):
    def test_multistep_requests_get_the_tool_turn(self):
        hint=candidate_constant('ESCALATE_HINT')
        for text in ['Organize my downloads by file type','Plan my week around the game','Research the best way to learn Rust and then summarize it','Compare these two models for me']:
            self.assertTrue(hint.search(text),text)
        for text in ['How are you doing?','What is overfitting?','Tell me a joke']:self.assertFalse(hint.search(text),text)
if __name__=='__main__':unittest.main()
