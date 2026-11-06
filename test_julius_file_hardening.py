import os, tempfile, time, unittest
from pathlib import Path
from julius_file_agent import FileAgent, FileProblem, human_size

class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = (Path(self.temp.name) / 'home').resolve()
        self.docs, self.downloads, self.desktop = (self.root / n for n in ('Documents', 'Downloads', 'Desktop'))
        for p in (self.docs, self.downloads, self.desktop): p.mkdir(parents=True)
        self.state = Path(self.temp.name) / 'state'; self.agent = self.make()
    def make(self, **kw): return FileAgent(self.root, self.state, **kw)
    def tearDown(self): self.temp.cleanup()
    def file(self, name, text='hello', folder=None, age_days=30):
        p = (folder or self.downloads) / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
        t = time.time() - age_days * 86400; os.utime(p, (t, t)); return p
    def apply(self, out, agent=None):
        agent = agent or self.agent; self.assertTrue(out['ok'], out); pid = out['data']['plan']['id']
        r = agent.execute(pid, confirmed=True, turn=out['data']['plan']['turn'] + 1); self.assertTrue(r['ok'], r); return r

class PreviewTests(Base):
    def test_human_size(self):
        self.assertEqual(human_size(10), '10 bytes'); self.assertEqual(human_size(1536), '1.5 KB'); self.assertEqual(human_size(5 * 1024 ** 2), '5 MB'); self.assertEqual(human_size(3 * 1024 ** 3), '3 GB')
    def test_preview_names_the_files_and_total_before_anything_changes(self):
        for n in ('a.txt', 'b.txt', 'c.txt', 'd.txt', 'e.txt'): self.file(n, 'x' * 2048)
        out = self.agent.propose('move', source='Downloads', destination=str(self.docs / 'Archive'), pattern='*.txt', turn=1)
        self.assertTrue(out['ok']); m = out['message']
        self.assertIn('a.txt, b.txt and c.txt', m); self.assertIn('and 2 more', m); self.assertIn('10 KB in total', m); self.assertIn('Nothing has changed', m)
        self.assertTrue((self.downloads / 'a.txt').exists() and not (self.docs / 'Archive').exists())
    def test_a_file_edited_after_the_preview_blocks_the_plan(self):
        p = self.file('a.txt'); out = self.agent.propose('move', source='Downloads', destination=str(self.docs / 'Archive'), pattern='a.txt', turn=1)
        p.write_text('edited after preview, different size')
        r = self.agent.execute(out['data']['plan']['id'], confirmed=True, turn=2)
        self.assertFalse(r['ok']); self.assertEqual(r['code'], 'changed'); self.assertTrue(p.exists())
    def test_confirming_in_the_same_turn_as_the_preview_is_refused(self):
        self.file('a.txt'); out = self.agent.propose('move', source='Downloads', destination=str(self.docs / 'Archive'), pattern='a.txt', turn=1)
        self.assertFalse(self.agent.execute(out['data']['plan']['id'], confirmed=True, turn=1)['ok'])
if __name__ == '__main__': unittest.main()
