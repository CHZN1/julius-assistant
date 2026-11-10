import unittest
from unittest.mock import Mock,patch
from julius.audio.asr_resources import recognition_resources
class RecognitionResourceTests(unittest.TestCase):
    def test_failure_releases_model_and_restores_limits(self):
        mx=Mock();mx.set_cache_limit.side_effect=[42,None];mx.set_memory_limit.side_effect=[99,None]
        holder=Mock();module=Mock(ModelHolder=holder)
        with patch.dict('sys.modules',{'mlx':Mock(core=mx),'mlx.core':mx}),patch('importlib.import_module',return_value=module):
            with self.assertRaises(RuntimeError):
                with recognition_resources():raise RuntimeError('transcription failed')
        self.assertIsNone(holder.model);self.assertIsNone(holder.model_path)
        self.assertEqual(mx.set_cache_limit.call_args.args,(42,));self.assertEqual(mx.set_memory_limit.call_args.args,(99,))
        mx.clear_cache.assert_called_once()
if __name__=='__main__':unittest.main()
