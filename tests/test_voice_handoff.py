"""Retained workers must acknowledge weight release; interrupted streams close."""
import io,json,threading,unittest
from unittest.mock import Mock,patch
from julius.audio.tts import SpeechEngine
class HandoffTests(unittest.TestCase):
    def engine(self,reply):
        e=SpeechEngine.__new__(SpeechEngine);e.backend='qwen-mlx';e._isolated_worker=True
        e.worker=Mock();e.worker.poll.return_value=None;e.worker.stdin=io.StringIO()
        e._worker_line=Mock(return_value=json.dumps(reply));e.close=Mock()
        return e
    @patch.dict('os.environ',{'JULIUS_MLX_KEEP_WORKER':'1'})
    def test_verified_release_retains_empty_worker(self):
        e=self.engine({'unloaded':True,'active_memory_bytes':0});worker=e.worker
        e.suspend();self.assertIs(e.worker,worker);e.close.assert_not_called()
        self.assertIn('unload',worker.stdin.getvalue())
    @patch.dict('os.environ',{'JULIUS_MLX_KEEP_WORKER':'1'})
    def test_invalid_or_large_release_closes_worker(self):
        for reply in ({'done':True},{'unloaded':True,'active_memory_bytes':200*1024**2}):
            e=self.engine(reply);e.suspend();e.close.assert_called_once();self.assertIsNone(e.worker)
    @patch.dict('os.environ',{'JULIUS_MLX_KEEP_WORKER':'1'})
    def test_active_stream_and_cancel_never_retain_worker(self):
        for cancelled in (False,True):
            e=self.engine({'unloaded':True,'active_memory_bytes':0})
            e._stream_active=not cancelled;e.cancel_event=threading.Event()
            if cancelled:e.cancel_event.set()
            e.suspend();e._worker_line.assert_not_called();e.close.assert_called_once()
if __name__=='__main__':unittest.main()
