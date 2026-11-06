import unittest
import julius_hotkey
class Fake:
    def __init__(self,mapping):self.mapping=mapping;self.started=False
    def start(self):self.started=True
class HotkeyTests(unittest.TestCase):
    def test_registers_the_combo_and_calls_stop(self):
        calls=[];made=[];say=[].append
        def factory(m):l=Fake(m);made.append(l);return l
        l=julius_hotkey.start(lambda:calls.append(1),'<ctrl>+<alt>+s',say=say,factory=factory)
        self.assertTrue(l.started);l.mapping['<ctrl>+<alt>+s']();self.assertEqual(calls,[1])
    def test_unavailable_listener_is_reported_not_fatal(self):
        out=[]
        def factory(m):raise RuntimeError('no permission')
        self.assertIsNone(julius_hotkey.start(lambda:None,say=out.append,factory=factory));self.assertIn('/stop',out[0])
if __name__=='__main__':unittest.main()
