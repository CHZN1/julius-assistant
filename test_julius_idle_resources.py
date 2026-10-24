import ast,contextlib,unittest
from pathlib import Path
from unittest.mock import Mock
class IdleReleaseTests(unittest.TestCase):
 def runtime(self):
  fn=next(n for n in ast.parse(Path('julius_candidate.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='release_idle_models')
  events=[];tts=Mock();tts.suspend.side_effect=lambda:events.append('voice')
  @contextlib.contextmanager
  def recognition():events.append('recognition');yield
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
if __name__=='__main__':unittest.main()
