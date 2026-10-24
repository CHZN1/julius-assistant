import ast,unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
from julius_microphone_gate import MicrophoneGate
class GateTests(unittest.TestCase):
 def test_playback_tail_and_failure_are_muted(self):
  now=[10.];gate=MicrophoneGate(clock=lambda:now[0])
  self.assertTrue(gate.accepting())
  with self.assertRaises(RuntimeError):
   with gate.output():
    self.assertFalse(gate.accepting())
    with gate.output():self.assertFalse(gate.accepting())
    self.assertFalse(gate.accepting());raise RuntimeError('failed playback')
  self.assertFalse(gate.accepting());now[0]+=.36;self.assertTrue(gate.accepting())
 def test_actual_callback_drops_playback_and_retains_user_audio(self):
  from collections import deque
  now=[10.];gate=MicrophoneGate(clock=lambda:now[0]);queue=deque()
  tree=ast.parse(Path('julius_candidate.py').read_text())
  fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='cb')
  scope={'microphone_gate':gate,'q':queue,'time':Mock(perf_counter=lambda:now[0])};exec(compile(ast.Module(body=[fn],type_ignores=[]),'callback','exec'),scope)
  audio=np.ones((100,1))
  with gate.output():scope['cb'](audio,100,None,None)
  scope['cb'](audio,100,None,None);self.assertFalse(queue)
  now[0]+=.36;scope['cb'](audio,100,None,None);self.assertEqual(len(queue),1)
  samples,captured_at=queue[0]
  self.assertEqual(captured_at,now[0]);audio[:]=0
  self.assertTrue(np.all(samples==1))
 def test_actual_speech_failure_releases_gate(self):
  now=[10.];gate=MicrophoneGate(clock=lambda:now[0])
  engine=Mock(backend='qwen-mlx');engine.synthesize.side_effect=RuntimeError('voice failed')
  tree=ast.parse(Path('julius_candidate.py').read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='speak_sentence')
  scope={'microphone_gate':gate,'tts':engine,'SPEED':1.,'clean_for_speech':lambda x:x,'close_backend':Mock()}
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'speech','exec'),scope)
  with self.assertRaises(RuntimeError):scope['speak_sentence']('Hello.')
  self.assertFalse(gate.accepting());now[0]+=.36;self.assertTrue(gate.accepting())
if __name__=='__main__':unittest.main()
