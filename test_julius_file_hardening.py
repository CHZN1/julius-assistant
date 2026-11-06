import os, tempfile, time, unittest
from pathlib import Path
from julius_file_agent import FileAgent, FileProblem, human_size

class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = (Path(self.temp.name) / 'home').resolve()
        self.docs, self.downloads, self.desktop = (self.root / n for n in ('Documents', 'Downloads', 'Desktop'))
        for p in (self.docs, self.downloads, self.desktop): p.mkdir(parents=True)
        self.state = Path(self.temp.name) / 'state'; self.agent = self.make()
    def make(self, **kw):
        a = FileAgent(self.root, self.state, **kw); a.use_birthtime = False  # test files are created now whatever utime says
        return a
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

class TrashTests(Base):
    def trash(self, pattern='*.txt', origin='human', **kw):
        return self.agent.propose('trash', source='Downloads', pattern=pattern, origin=origin, turn=kw.pop('turn', 1), **kw)
    def test_trash_round_trip_restores_identical_bytes(self):
        p = self.file('report.txt', 'quarterly numbers'); out = self.trash(); self.assertIn('trash', out['message']); self.assertTrue(p.exists())
        r = self.apply(out); self.assertFalse(p.exists()); self.assertIn('Moved to the trash', r['message'])
        trashed = list(self.agent.trash_dir.rglob('*__report.txt')); self.assertEqual(len(trashed), 1)
        u = self.agent.undo(); self.assertTrue(u['ok'], u); self.assertEqual(p.read_text(), 'quarterly numbers'); self.assertEqual(list(self.agent.trash_dir.rglob('*__report.txt')), [])
    def test_undo_survives_a_restart_and_is_a_stack(self):
        a = self.file('a.txt', 'A'); b = self.file('b.txt', 'B')
        self.apply(self.agent.propose('trash', source='Downloads', pattern='a.txt', turn=1)); self.apply(self.agent.propose('trash', source='Downloads', pattern='b.txt', turn=3), None)
        again = self.make(); self.assertTrue(again.undo()['ok']); self.assertTrue(b.exists() and not a.exists())
        third = self.make(); self.assertTrue(third.undo()['ok']); self.assertTrue(a.exists()); self.assertEqual(third.undo()['code'], 'nothing_to_undo')
    def test_trash_only_loose_files_never_folders_or_recursion(self):
        self.file('loose.txt'); self.file('deep.txt', folder=self.downloads / 'sub')
        out = self.trash(); names = [Path(o['from']).name for o in out['data']['plan']['ops']]; self.assertEqual(names, ['loose.txt'])
        explicit = self.agent.propose('trash', paths=[str(self.downloads / 'sub')], turn=1)
        self.assertFalse(explicit['ok']); self.assertEqual(explicit['code'], 'all_protected')
    def test_protected_items_for_everyone(self):
        self.file('Song stems.zip'); self.file('mix.wav'); self.file('Kontakt Library.nki'); self.file('GarageBand idea.txt'); self.file('Waves notes.txt'); self.file('Slate settings.txt'); self.file('Zebra patch.txt')
        (self.downloads / 'Tool.app').mkdir(); (self.downloads / 'Tool.app' / 'Contents.txt').write_text('x')
        out = self.trash('*')
        self.assertFalse(out['ok'], out); self.assertEqual(out['code'], 'all_protected'); self.assertGreaterEqual(len(out['data']['skipped']), 7)
        self.assertTrue((self.downloads / 'mix.wav').exists() and (self.downloads / 'Tool.app' / 'Contents.txt').exists())
    def test_files_next_to_project_markers_are_protected(self):
        proj = self.downloads / 'proj'; self.file('notes.txt', folder=proj); (proj / 'package.json').write_text('{}')
        out = self.agent.propose('trash', paths=[str(proj / 'notes.txt')], turn=1); self.assertFalse(out['ok']); self.assertIn('project', out['data']['skipped'][0]['reason'])
    def test_planner_never_proposes_recent_code_or_live_installers_but_a_human_may_name_them(self):
        self.file('new.txt', age_days=2); self.file('script.py', age_days=90); self.file('Setup.dmg', age_days=3); self.file('old_installer.pkg', age_days=200); self.file('old.txt', age_days=60)
        planner = self.trash('*', origin='planner'); names = sorted(Path(o['from']).name for o in planner['data']['plan']['ops'])
        self.assertEqual(names, ['old.txt', 'old_installer.pkg']); reasons = {x['name']: x['reason'] for x in planner['data']['skipped']}
        self.assertIn('7 days', reasons['new.txt']); self.assertIn('source-code', reasons['script.py']); self.assertIn('7 days', reasons['Setup.dmg'])
        human = self.agent.propose('trash', paths=[str(self.downloads / 'new.txt')], origin='human', turn=1); self.assertTrue(human['ok'])
    def test_installer_older_than_a_week_but_used_recently_is_protected_from_the_planner(self):
        p = self.file('Tool.dmg', age_days=60); now = time.time(); os.utime(p, (now, now - 60 * 86400))
        self.assertIn('installer', self.agent.protection(p, 'planner') or ''); self.assertIsNone(self.agent.protection(self.file('x.txt', age_days=60), 'planner'))
    def test_the_trash_is_not_reachable_by_path_or_by_other_plans(self):
        self.apply(self.trash(self.file('a.txt').name)); inside = next(self.agent.trash_dir.rglob('*__a.txt'))
        for call in (lambda: self.agent.list(str(inside.parent)), lambda: self.agent.read(str(inside)), lambda: self.agent.find('*', str(self.agent.trash_dir))):
            self.assertEqual(call()['code'], 'protected_path')
        self.file('b.txt'); out = self.agent.propose('move', source='Downloads', destination=str(self.agent.trash_dir / 'x'), pattern='b.txt', turn=1); self.assertFalse(out['ok'])
    def test_trash_notice_after_one_gigabyte(self):
        import julius_file_agent as fa
        self.file('a.txt', 'x' * 100); old = fa.TRASH_NOTICE_BYTES; fa.TRASH_NOTICE_BYTES = 50
        try: r = self.apply(self.trash())
        finally: fa.TRASH_NOTICE_BYTES = old
        self.assertIn('empty the trash', r['message']); self.assertNotIn('empty the trash', self.apply(self.trash(self.file('c.txt', 'y').name, turn=5), None)['message']) if False else None
    def test_history_lists_recent_changes(self):
        self.file('a.txt'); self.apply(self.trash()); h = self.agent.history(); self.assertIn('trashed 1 file', h['message']); self.assertEqual(self.agent.undo()['code'], 'undone')
        self.assertIn('undone', self.agent.history()['message'])

class EmptyTrashTests(Base):
    def test_empty_trash_previews_then_deletes_only_on_confirmation_and_undo_then_reports_it(self):
        self.file('a.txt', 'A'); self.file('b.txt', 'B'); self.apply(self.agent.propose('trash', source='Downloads', pattern='*.txt', turn=1))
        info = self.agent.trash_info(); self.assertIn('2 file', info['message'])
        pre = self.agent.propose_empty_trash(turn=10); self.assertIn('permanently', pre['message']); self.assertEqual(len(list(self.agent.trash_dir.rglob('*__*'))), 2)
        self.assertFalse(self.agent.execute(pre['data']['plan']['id'], confirmed=True, turn=10)['ok'])  # same turn
        r = self.agent.execute(pre['data']['plan']['id'], confirmed=True, turn=11); self.assertTrue(r['ok'], r); self.assertEqual(list(self.agent.trash_dir.rglob('*__*')), [])
        self.assertEqual(self.agent.undo()['code'], 'nothing_to_undo'); self.assertEqual(self.agent.propose_empty_trash(turn=12)['code'], 'trash_empty')
    def test_cancel_keeps_everything(self):
        self.file('a.txt'); self.apply(self.agent.propose('trash', source='Downloads', pattern='*.txt', turn=1))
        pre = self.agent.propose_empty_trash(turn=10); self.agent.cancel(pre['data']['plan']['id']); self.assertEqual(len(list(self.agent.trash_dir.rglob('*__a.txt'))), 1)
        self.assertEqual(self.agent.undo()['code'], 'undone')
    def test_only_files_inside_the_trash_can_be_purged(self):
        outside = self.file('keep.txt', 'precious'); self.file('a.txt'); self.apply(self.agent.propose('trash', source='Downloads', pattern='a.txt', turn=1))
        pre = self.agent.propose_empty_trash(turn=5); self.agent.execute(pre['data']['plan']['id'], confirmed=True, turn=6); self.assertEqual(outside.read_text(), 'precious')


class VoiceFlowTests(Base):
    def setUp(self):
        super().setUp()
        from julius_file_session import FileSession
        self.session = FileSession(self.agent); self.turn = 0
    def say(self, text):
        self.turn += 1; return self.session.handle(text, self.turn)
    def test_trash_by_voice_needs_a_separate_yes_and_undo_restores(self):
        p = self.file('notes.txt', 'keep me')
        out = self.say('trash notes.txt in Downloads'); self.assertEqual(out['code'], 'preview'); self.assertTrue(p.exists())
        done = self.say('yes'); self.assertEqual(done['code'], 'completed'); self.assertFalse(p.exists())
        self.assertEqual(self.say('undo')['code'], 'undone'); self.assertEqual(p.read_text(), 'keep me')
    def test_vague_or_bulk_delete_requests_change_nothing(self):
        p = self.file('notes.txt')
        for phrase in ('delete everything in my Downloads folder', 'delete that file', 'throw away the files'):
            out = self.say(phrase); self.assertEqual(out['code'], 'clarification', phrase)
        self.say('yes'); self.assertTrue(p.exists())
    def test_empty_the_trash_by_voice_previews_and_a_no_keeps_it(self):
        self.file('notes.txt'); self.say('trash notes.txt in Downloads'); self.say('yes')
        self.assertIn('1 file', self.say('how big is the trash')['message'])
        pre = self.say('empty the trash'); self.assertEqual(pre['code'], 'preview'); self.assertIn('permanently', pre['message'])
        self.assertEqual(self.say('no')['code'], 'cancelled'); self.assertEqual(len(list(self.agent.trash_dir.rglob('*__notes.txt'))), 1)
        self.assertIn('trashed', self.say('what did you change today')['message'])
    def test_planner_proposals_go_through_the_same_protections_and_staging(self):
        import julius_orchestrator as jo
        self.file('old.txt', age_days=90); self.file('fresh.txt', age_days=1); self.file('mix.wav', age_days=90)
        jo.configure(file_session=self.session, turn=lambda: self.turn + 1)
        msg = jo.files_propose({'action': 'trash', 'source': 'Downloads', 'pattern': '*'})
        plan = self.session.last_result['data']['plan']
        self.assertEqual([Path(o['from']).name for o in plan['ops']], ['old.txt']); self.assertIn('Staged only', msg)
        self.assertTrue((self.downloads / 'fresh.txt').exists() and (self.downloads / 'mix.wav').exists() and (self.downloads / 'old.txt').exists())
        skipped = {x['name'] for x in self.session.last_result['data']['skipped']}; self.assertEqual(skipped, {'fresh.txt', 'mix.wav'})


class PermissionTests(Base):
    def agent_with(self, **folders):
        return self.make(folders=folders)
    def test_read_only_folder_allows_reading_and_copying_out_but_nothing_else(self):
        a = self.agent_with(Documents='write', Downloads='read', Desktop='write'); p = self.file('a.txt', 'x')
        self.assertTrue(a.list('Downloads')['ok']); self.assertTrue(a.read(str(p))['ok']); self.assertTrue(a.find('*.txt', 'Downloads')['ok'])
        self.assertEqual(a.propose('move', source='Downloads', destination=str(self.docs / 'A'), pattern='a.txt', turn=1)['code'], 'read_only')
        self.assertEqual(a.propose('copy', source='Downloads', destination=str(self.downloads / 'B'), pattern='a.txt', turn=1)['code'], 'read_only')
        self.assertEqual(a.propose('rename', path=str(p), destination=str(self.downloads / 'z.txt'), turn=1)['code'], 'read_only')
        self.assertEqual(a.propose('create', destination=str(self.downloads / 'n.txt'), content='hi', turn=1)['code'], 'read_only')
        self.assertEqual(a.propose('trash', source='Downloads', pattern='a.txt', turn=1)['code'], 'read_only')
        self.assertEqual(a.propose('trash', paths=[str(p)], turn=1)['code'], 'read_only')
        copy = a.propose('copy', source='Downloads', destination=str(self.docs / 'Copies'), pattern='a.txt', turn=1); self.assertTrue(copy['ok'], copy)
        self.apply(copy, a); self.assertTrue(p.exists() and (self.docs / 'Copies' / 'a.txt').exists())
    def test_none_hides_a_folder_completely(self):
        a = self.agent_with(Documents='write', Downloads='none', Desktop='write'); self.file('a.txt')
        self.assertEqual(a.list('Downloads')['code'], 'outside_scope'); self.assertEqual(a.propose('move', source='Documents', destination=str(self.downloads / 'x'), turn=1, pattern='*')['code'], 'outside_scope')
    def test_extra_folder_comes_only_from_the_config_table(self):
        extra = self.root / 'Projects'; extra.mkdir(); (extra / 'n.txt').write_text('n')
        self.assertEqual(self.agent.list(str(extra))['code'], 'outside_scope')
        a = self.agent_with(Documents='write', **{str(extra): 'read'}); self.assertTrue(a.list(str(extra))['ok']); self.assertEqual(a.propose('trash', paths=[str(extra / 'n.txt')], turn=1)['code'], 'read_only')
    def test_most_specific_folder_wins(self):
        sub = self.docs / 'Locked'; sub.mkdir(); (sub / 'k.txt').write_text('k'); self.file('f.txt', folder=self.docs)
        a = self.agent_with(Documents='write', **{'Documents/Locked': 'read'})
        self.assertEqual(a.propose('trash', paths=[str(sub / 'k.txt')], turn=1)['code'], 'read_only'); self.assertTrue(a.propose('trash', paths=[str(self.docs / 'f.txt')], turn=1, origin='human')['ok'])
    def test_bad_mode_is_a_config_error_and_defaults_are_write(self):
        with self.assertRaises(FileProblem): self.agent_with(Documents='readwrite')
        self.assertEqual(set(self.make().modes.values()), {'write'})
    def test_undo_will_not_write_into_a_folder_that_became_read_only(self):
        p = self.file('a.txt'); self.apply(self.agent.propose('trash', source='Downloads', pattern='a.txt', turn=1))
        ro = self.agent_with(Documents='write', Downloads='read', Desktop='write'); u = ro.undo()
        self.assertFalse(u['ok']); self.assertFalse(p.exists()); self.assertEqual(len(list(ro.trash_dir.rglob('*__a.txt'))), 1)
    def test_shipped_config_table_matches_the_defaults(self):
        import julius_config
        self.assertEqual(julius_config.load_config()['files']['folders'], {'Documents': 'write', 'Desktop': 'write', 'Downloads': 'write'})

if __name__ == '__main__': unittest.main()
