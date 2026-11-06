"""Exercise actual runtime recovery paths without models or devices."""
import ast,contextlib,io,time,unittest
from pathlib import Path
from unittest.mock import Mock
from julius_cancellation import Cancellation,TurnCancelled
from julius_listening import incomplete_fragment
from julius_conversation import authority_discussion_reply

class TurnRecoveryTests(unittest.TestCase):
    def runtime(self):
        ns={'time':time,'cancellation':Cancellation(),'TurnCancelled':TurnCancelled,
            'authority_discussion_reply':authority_discussion_reply,'incomplete_fragment':incomplete_fragment,'eyes':Mock(),'TURN':{'n':0},
            'jf':Mock(),'jt':Mock(),'jm':Mock(),'history':[], '_validation_output':None,
            'handle_files':Mock(return_value=None),'handle_memory':Mock(return_value=None),'memory_autocapture':Mock(),'speak':Mock(),'speak_sentence':Mock(),
            'ask_streaming':Mock(return_value='A response.'),'close_backend':Mock(),
            'tts':Mock(backend='qwen-mlx')}
        ns['eyes'].snapshot.return_value={};ns['eyes'].describe.return_value=''
        tree=ast.parse(Path('julius_candidate.py').read_text())
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'process','_process'}],type_ignores=[]),'runtime','exec'),ns)
        return ns
    def test_cancelled_reasoning_unloads_without_speaking_error(self):
        ns=self.runtime();ns['ask_streaming'].side_effect=TurnCancelled('stop')
        with contextlib.redirect_stdout(io.StringIO()):ns['process']('Explain that.','typed')
        ns['close_backend'].assert_called_once();ns['speak'].assert_not_called();ns['speak_sentence'].assert_not_called()
        self.assertFalse(ns['cancellation'].busy.is_set())
        def reply(*args,**kwargs):
            kwargs['speak_fn']('A response.')
            return 'A response.'
        ns['ask_streaming'].side_effect=reply
        with contextlib.redirect_stdout(io.StringIO()):ns['process']('Try the next question.','typed')
        ns['speak'].assert_called_once_with('A response.')
    def test_model_failure_unloads_before_recovery_speech(self):
        ns=self.runtime();order=[]
        ns['ask_streaming'].side_effect=RuntimeError('model failed')
        ns['close_backend'].side_effect=lambda:order.append('unload')
        ns['speak'].side_effect=lambda _:order.append('speak')
        with contextlib.redirect_stdout(io.StringIO()):ns['process']('Explain that.','typed')
        self.assertEqual(order,['unload','speak'])
    def test_incomplete_input_asks_without_reasoning(self):
        ns=self.runtime()
        with contextlib.redirect_stdout(io.StringIO()):ns['process']('You.','en')
        ns['ask_streaming'].assert_not_called();ns['handle_files'].assert_not_called()
        self.assertIn('say that again',ns['speak'].call_args.args[0])
    def test_voice_does_not_load_if_reasoning_release_fails(self):
        ns={'tts':Mock(backend='qwen-mlx'),'close_backend':Mock(side_effect=RuntimeError('unload failed')),
            'clean_for_speech':lambda x:x}
        tree=ast.parse(Path('julius_candidate.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='speak_sentence')
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'speech','exec'),ns)
        with self.assertRaisesRegex(RuntimeError,'unload failed'):ns['speak_sentence']('Hello.')
        ns['tts'].synthesize.assert_not_called()
