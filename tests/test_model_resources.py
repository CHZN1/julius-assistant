import unittest
from unittest.mock import Mock
from julius.audio.model_resources import ModelResources, ResourceUnavailable

class ResourceHandoffTests(unittest.TestCase):
    def setup_manager(self):
        m=ModelResources(snapshot=Mock(return_value={'free_percent':60,'swap_mb':0}))
        self.voice=Mock();self.brain=Mock();self.coder=Mock()
        for name,fn in [('voice',self.voice),('brain',self.brain),('coder',self.coder)]:m.register(name,fn)
        return m
    def test_specialist_releases_residents_before_work_and_after_failure(self):
        m=self.setup_manager()
        with self.assertRaisesRegex(RuntimeError,'generation failed'):
            with m.job('coder'):
                self.voice.assert_called_once();self.brain.assert_called_once()
                self.assertEqual(m.active,'coder')
                raise RuntimeError('generation failed')
        self.assertEqual(self.coder.call_count,2);self.assertIsNone(m.active)
        with m.job('voice'):self.assertEqual(m.active,'voice')
    def test_overlapping_job_cannot_unload_active_model(self):
        m=self.setup_manager()
        with m.job('coder'):
            calls=self.coder.call_count
            with self.assertRaises(ResourceUnavailable):
                with m.job('voice'):self.fail('Overlapping work started')
            self.assertEqual(self.coder.call_count,calls)
    def test_pressure_and_failed_unload_prevent_work(self):
        m=self.setup_manager();m.snapshot.return_value={'free_percent':20,'swap_mb':0}
        with self.assertRaises(ResourceUnavailable):
            with m.job('coder'):self.fail('Started under pressure')
        m.snapshot.return_value={'free_percent':60,'swap_mb':0};self.voice.side_effect=RuntimeError('unload failed')
        with self.assertRaisesRegex(RuntimeError,'unload failed'):
            with m.job('coder'):self.fail('Started with resident model')
        self.assertIsNone(m.active)
