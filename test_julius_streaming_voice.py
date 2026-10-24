"""Streaming protocol recovery, buffered IPC, and continuous output contracts."""
import ast, io, json, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
from julius_tts import SpeechEngine

class StreamingProtocolTests(unittest.TestCase):
    def test_reminder_normalization_preserves_body_and_reference_text(self):
        from julius_mlx_voice_support import normalize_speech
        from julius_voice_identity import REF_TEXT
        self.assertEqual(normalize_speech('Reminder: the user, check this.'),'Here is your reminder. the user, check this.')
        self.assertEqual(normalize_speech(REF_TEXT),REF_TEXT)
        self.assertEqual(normalize_speech('Your reminder: check this.'),'Your reminder: check this.')
    def engine(self, messages):
        e=SpeechEngine.__new__(SpeechEngine)
        e.backend='qwen-mlx';e._isolated_worker=True;e.worker=Mock()
        e.worker.poll.return_value=None;e.worker.stdin=io.StringIO()
        e._worker_line=Mock(side_effect=[json.dumps(m) for m in messages])
        e.suspend=Mock();return e
    def test_all_chunks_consumed_before_next_request(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'audio.npy';np.save(path,np.ones(8,dtype=np.float32))
            chunk={'chunk':True,'path':str(path),'sample_rate':24000}
            e=self.engine([chunk,chunk,{'done':True,'chunks':2}])
            result=list(e.synthesize('Hello.'))
            self.assertEqual(len(result),2);self.assertEqual(e.worker_metrics['chunks'],2)
            e.suspend.assert_not_called()
    def test_abandoned_stream_closes_worker(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'audio.npy';np.save(path,np.ones(8))
            e=self.engine([{'chunk':True,'path':str(path),'sample_rate':24000}])
            gen=e.synthesize('Hello.');next(gen);gen.close()
            e.suspend.assert_called_once()
    def test_empty_and_failed_streams_are_not_success(self):
        for messages in [[{'done':True}],[{'error':'numerical failure'}]]:
            e=self.engine(messages)
            with self.assertRaises(RuntimeError):list(e.synthesize('Hello.'))
            e.suspend.assert_called_once()
    def test_pipe_backlog_does_not_wait_for_another_os_read(self):
        e=SpeechEngine.__new__(SpeechEngine);e.worker=Mock();e._worker_buffer=b''
        with patch('select.select',return_value=([e.worker.stdout],[],[])) as poll, patch('os.read',return_value=b'first\nsecond\n') as read:
            self.assertEqual(e._worker_line(1),'first')
            self.assertEqual(e._worker_line(1),'second')
            self.assertEqual(read.call_count,1);self.assertEqual(poll.call_count,1)

class ContinuousPlaybackTests(unittest.TestCase):
    def speak(self, chunks):
        tree=ast.parse(Path('julius_candidate.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='speak_sentence')
        engine=Mock();engine.backend='qwen-mlx';engine.synthesize.return_value=iter(chunks)
        # Runtime generators support close; use a real generator in the harness.
        def generate():yield from chunks
        engine.synthesize.return_value=generate()
        sd=Mock();output=Mock();sd.OutputStream.return_value.__enter__=Mock(return_value=output)
        sd.OutputStream.return_value.__exit__=Mock(return_value=False)
        from julius_audio_output import ready_output
        sd.PortAudioError=RuntimeError
        scope={'ready_output':ready_output,'tts':engine,'sd':sd,'SPEED':1.05,'clean_for_speech':lambda x:x,'_validation_no_devices':False}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'speech','exec'),scope)
        return scope['speak_sentence'],sd,output
    def test_one_output_stream_for_multiple_chunks(self):
        speak,sd,output=self.speak([(np.zeros(8),24000),(np.ones(8),24000)])
        speak('Hello.');sd.OutputStream.assert_called_once();self.assertEqual(output.write.call_count,2)
        sd.play.assert_not_called()
    def test_rate_change_fails_instead_of_playing_at_wrong_rate(self):
        speak,_,_=self.speak([(np.zeros(8),24000),(np.ones(8),16000)])
        with self.assertRaises(RuntimeError):speak('Hello.')

if __name__=='__main__':unittest.main()
