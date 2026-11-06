import ast,contextlib,unittest,unittest.mock
from pathlib import Path
from unittest.mock import Mock
class IdleReleaseTests(unittest.TestCase):
 def runtime(self):
  fn=next(n for n in ast.parse(Path('julius_candidate.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='release_idle_models')
  events=[];tts=Mock();tts.suspend.side_effect=lambda:events.append('voice')
  @contextlib.contextmanager
  def recognition(release=True):events.append('recognition' if release else 'recognition-kept');yield
  ns={'tts':tts,'close_backend':lambda:events.append('brain'),'recognition_resources':recognition,'print':Mock()}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'idle','exec'),ns)
  return ns,events
 def test_sleep_releases_voice_brain_and_recognition(self):
  ns,events=self.runtime();self.assertTrue(ns['release_idle_models']())
  self.assertEqual(events,['voice','brain','recognition'])
 def test_failed_release_is_reported_without_loading_other_models(self):
  ns,events=self.runtime();ns['tts'].suspend.side_effect=RuntimeError('failed unload')
  self.assertFalse(ns['release_idle_models']());self.assertFalse(events)
  self.assertIn('idle release failed',ns['print'].call_args.args[0])
 def test_message_says_all_weights_released_with_a_local_brain(self):
  ns,events=self.runtime();ns['LLM_BACKEND']='ollama'
  with unittest.mock.patch.dict('os.environ',{'JULIUS_KEEP_VOICE':'0'}):ns['release_idle_models']()
  self.assertIn('idle model weights released',ns['print'].call_args.args[0])
 def test_cloud_profile_message_only_claims_speech_recognition_was_released(self):
  ns,events=self.runtime();ns['LLM_BACKEND']='cloud'
  with unittest.mock.patch.dict('os.environ',{'JULIUS_KEEP_VOICE':'1'}):ns['release_idle_models']()
  said=ns['print'].call_args.args[0];self.assertIn('speech recognition released',said);self.assertIn('voice stays loaded',said);self.assertNotIn('model weights released',said)

 def test_resident_whisper_stays_loaded_while_sleeping(self):
  ns,events=self.runtime();ns['LLM_BACKEND']='cloud'
  with unittest.mock.patch.dict('os.environ',{'JULIUS_KEEP_VOICE':'1','JULIUS_ASR_RESIDENT':'1'}):ns['release_idle_models']()
  self.assertIn('recognition-kept',events);self.assertNotIn('speech recognition released',ns['print'].call_args.args[0])
if __name__=='__main__':unittest.main()
