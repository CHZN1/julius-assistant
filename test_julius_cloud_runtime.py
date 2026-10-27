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
    def test_default_cloud_profile_uses_the_mlx_voice_and_torch_remains_a_fallback_profile(self):
        launcher=Path('run_julius.sh').read_text()
        cloud=launcher.split('  cloud) ')[1].split(';;')[0];torch=launcher.split('  cloud-torch) ')[1].split(';;')[0]
        self.assertIn('JULIUS_SILENCE_SECONDS:-0.6',cloud)
        self.assertIn('PROFILE_VOICE=qwen-mlx',cloud);self.assertIn('JULIUS_MLX_KEEP_WORKER',cloud);self.assertIn('PROFILE_VOICE=qwen-clone',torch)
        self.assertIn('JULIUS_KEEP_VOICE',torch)
        import subprocess;self.assertEqual(subprocess.run(['zsh','-n','run_julius.sh']).returncode,0)
class EscalationHintTests(unittest.TestCase):
    def test_multistep_requests_get_the_tool_turn(self):
        hint=candidate_constant('ESCALATE_HINT')
        for text in ['Organize my downloads by file type','Plan my week around the game','Research the best way to learn Rust and then summarize it','Compare these two models for me']:
            self.assertTrue(hint.search(text),text)
        for text in ['How are you doing?','What is overfitting?','Tell me a joke']:self.assertFalse(hint.search(text),text)
class AsrModelChoiceTests(unittest.TestCase):
    def asr_for(self,value):
        import ast
        src=ast.parse(Path('julius_candidate.py').read_text());ns={'os':os}
        for node in src.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('ASR_MODELS','ASR') for t in node.targets):
                exec(compile(ast.Module(body=[node],type_ignores=[]),'<c>','exec'),ns)
        return ns['ASR']
    def test_small_en_is_the_default_and_the_others_stay_selectable(self):
        with patch.dict(os.environ,{},clear=False):
            os.environ.pop('JULIUS_ASR_MODEL',None);self.assertEqual(self.asr_for(None),'mlx-community/whisper-small.en-mlx')
        for alias,repo in [('distil-large-v3','mlx-community/distil-whisper-large-v3'),('large-v3-turbo','mlx-community/whisper-large-v3-turbo'),('small.en','mlx-community/whisper-small.en-mlx')]:
            with patch.dict(os.environ,{'JULIUS_ASR_MODEL':alias}):self.assertEqual(self.asr_for(alias),repo)
        with patch.dict(os.environ,{'JULIUS_ASR_MODEL':'someone/whisper-custom'}):self.assertEqual(self.asr_for('x'),'someone/whisper-custom')
if __name__=='__main__':unittest.main()
