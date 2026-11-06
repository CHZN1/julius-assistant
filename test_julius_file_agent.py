"""Real temporary filesystem tests; no personal files, devices, or inference."""
import ast, contextlib, io, json, os, re, tempfile, time, unittest
from pathlib import Path
from unittest.mock import patch
from julius_file_agent import FileAgent, FileProblem
from julius_file_session import FileSession
from julius_router import route, parse, match_dir, norm, _dirs

class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=(Path(self.temp.name)/'home').resolve();self.docs=self.root/'Documents';self.downloads=self.root/'Downloads'
        for p in [self.docs,self.downloads,self.root/'Desktop']:p.mkdir(parents=True)
        self.agent=FileAgent(self.root,Path(self.temp.name)/'state')
        self.out=self.docs/'Archive'
    def tearDown(self):self.temp.cleanup()
    def file(self,name='note.txt',text='hello',folder=None):
        p=(folder or self.docs)/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text);return p
    def plan(self,action='move',**kwargs):
        r=self.agent.propose(action,destination=kwargs.pop('destination',str(self.out)),turn=1,**kwargs)
        self.assertTrue(r['ok'],r);return r['data']['plan']['id']
    def runplan(self,pid):
        r=self.agent.execute(pid,confirmed=True,turn=2);self.assertTrue(r['ok'],r);return r

class ScopeAndReadTests(Fixture):
    def test_component_scope_not_prefix(self):
        outside=self.root/'Documents-evil';outside.mkdir();self.file(folder=outside)
        self.assertEqual(self.agent.read(str(outside/'note.txt'))['code'],'outside_scope')
    def test_traversal_is_blocked(self):
        self.assertEqual(self.agent.list('Documents/../../')['code'],'outside_scope')
    def test_symlink_escape_and_dangling_link(self):
        (self.docs/'link').symlink_to(self.root/'Desktop',target_is_directory=True)
        (self.docs/'dead').symlink_to(self.root/'missing')
        self.assertEqual(self.agent.list('Documents/link')['code'],'symlink')
        self.assertEqual(self.agent.propose('create',destination='Documents/dead',content='x')['code'],'symlink')
    def test_symlinks_and_hidden_excluded_from_search(self):
        self.file();self.file('.secret');(self.docs/'alias').symlink_to(self.docs/'note.txt')
        self.assertEqual([x['name'] for x in self.agent.find()['data']['items']],['note.txt'])
    def test_ambiguous_bare_path_does_not_guess(self):
        (self.root/'Reports').mkdir();(self.docs/'Reports').mkdir()
        self.assertEqual(self.agent.list('Reports')['code'],'ambiguous_path')
    def test_multiple_extensions_and_case(self):
        for n in ['a.JPG','b.png','c.txt']:self.file(n)
        self.assertEqual(len(self.agent.find(['*.jpg','*.png'])['data']['items']),2)
    def test_bounded_scan_reports_incomplete(self):
        for i in range(10):self.file(f'{i}.txt')
        self.agent.MAX_ENTRIES=4
        r=self.agent.find();self.assertTrue(r['data']['truncated']);self.assertLessEqual(r['data']['visited_entries'],4)
        self.assertEqual(self.agent.propose('move',destination=str(self.out))['code'],'incomplete_selection')
    def test_result_cap_prevents_partial_batch_selection(self):
        for i in range(5):self.file(f'{i}.txt')
        self.agent.MAX_PLAN_FILES=2
        self.assertEqual(self.agent.propose('move',destination=str(self.out))['code'],'incomplete_selection')
    def test_read_is_bounded_and_utf8_preserved(self):
        p=self.file(text='é'*10000);r=self.agent.read(str(p),11)
        self.assertEqual(r['data']['text'],'é'*11);self.assertTrue(r['data']['truncated'])
    def test_binary_is_not_spoken_as_text(self):
        p=self.docs/'bin';p.write_bytes(b'x\x00y');self.assertEqual(self.agent.read(str(p))['code'],'binary_file')
    def test_listing_reports_truncation(self):
        for i in range(5):self.file(f'{i}.txt')
        r=self.agent.list(limit=2);self.assertTrue(r['data']['truncated']);self.assertEqual(len(r['data']['items']),2)

class MutationTests(Fixture):
    def test_preview_does_not_move_and_confirmation_required(self):
        p=self.file();pid=self.plan()
        self.assertTrue(p.exists());self.assertFalse(self.out.exists())
        self.assertEqual(self.agent.execute(pid,turn=2)['code'],'confirmation_required')
        self.assertEqual(self.agent.execute(pid,confirmed=True,turn=1)['code'],'confirmation_required')
        self.runplan(pid);self.assertFalse(p.exists());self.assertEqual((self.out/p.name).read_text(),'hello')
    def test_changed_selection_blocks_whole_batch(self):
        a=self.file('a.txt');b=self.file('b.txt');pid=self.plan();b.write_text('changed')
        self.assertEqual(self.agent.execute(pid,confirmed=True,turn=2)['code'],'changed');self.assertTrue(a.exists());self.assertFalse(self.out.exists())
    def test_same_metadata_different_content_is_detected(self):
        p=self.file(text='aaaa');pid=self.plan();s=p.stat();p.write_text('bbbb');os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns))
        self.assertEqual(self.agent.execute(pid,confirmed=True,turn=2)['code'],'changed')
    def test_destination_appearing_after_preview_blocks_batch(self):
        self.file();pid=self.plan();self.file(text='original',folder=self.out)
        self.assertEqual(self.agent.execute(pid,confirmed=True,turn=2)['code'],'collision');self.assertEqual((self.out/'note.txt').read_text(),'original')
    def test_exclusive_publication_handles_late_collision(self):
        self.file();pid=self.plan();original=self.agent._publish
        def race(op,action,**kwargs):
            self.file(text='other app',folder=self.out);return original(op,action,**kwargs)
        with patch.object(self.agent,'_publish',side_effect=race):r=self.agent.execute(pid,confirmed=True,turn=2)
        self.assertEqual(r['code'],'partial');self.assertEqual((self.out/'note.txt').read_text(),'other app');self.assertTrue((self.docs/'note.txt').exists())
    def test_recursive_move_preserves_subdirectories(self):
        self.file('note.txt','a',self.docs/'one');self.file('note.txt','b',self.docs/'two');pid=self.plan(recursive=True);self.runplan(pid)
        self.assertEqual((self.out/'one/note.txt').read_text(),'a');self.assertEqual((self.out/'two/note.txt').read_text(),'b')
    def test_copy_leaves_source_and_undo_removes_only_copy(self):
        p=self.file();pid=self.plan('copy');self.runplan(pid);self.assertTrue(p.exists());r=self.agent.undo();self.assertTrue(r['ok'],r);self.assertTrue(p.exists());self.assertFalse((self.out/p.name).exists())
    def test_create_and_undo(self):
        target=self.docs/'new.txt';pid=self.plan('create',destination=str(target),content='new text');self.runplan(pid);self.assertEqual(target.read_text(),'new text');self.assertTrue(self.agent.undo()['ok']);self.assertFalse(target.exists())
    def test_rename_and_undo(self):
        p=self.file();dst=self.docs/'renamed.txt';pid=self.plan('rename',path=str(p),destination=str(dst));self.runplan(pid);self.assertTrue(dst.exists());self.assertTrue(self.agent.undo()['ok']);self.assertTrue(p.exists())
    def test_unknown_or_cancelled_plan_never_runs(self):
        self.file();pid=self.plan();self.agent.cancel(pid)
        self.assertEqual(self.agent.execute(pid,confirmed=True,turn=2)['code'],'already_processed')
        self.assertEqual(self.agent.execute('made-up',confirmed=True,turn=2)['code'],'unknown_plan')
    def test_previous_session_and_expired_plan_rejected(self):
        self.file();pid=self.plan();other=FileAgent(self.root,self.agent.state_dir)
        self.assertEqual(other.execute(pid,confirmed=True,turn=2)['code'],'stale_session')
        with patch('julius_file_agent.time.time',return_value=time.time()+3600):self.assertEqual(self.agent.execute(pid,confirmed=True,turn=2)['code'],'expired')
    def test_oversize_checked_before_hashing(self):
        self.file(text='abc');self.agent.MAX_PLAN_BYTES=2
        with patch.object(self.agent,'_fingerprint',side_effect=AssertionError('must not hash')):
            self.assertEqual(self.agent.propose('move',destination=str(self.out))['code'],'too_large')
    def test_double_execute_is_idempotent(self):
        self.file();pid=self.plan();self.runplan(pid)
        self.assertEqual(self.agent.execute(pid,confirmed=True,turn=3)['code'],'already_processed')

class UndoAndRecoveryTests(Fixture):
    def test_repeated_undo_moves_to_earlier_batch(self):
        a=self.file('a.txt');pid=self.plan(pattern='a.txt');self.runplan(pid)
        b=self.file('b.txt');pid=self.plan(pattern='b.txt');self.runplan(pid)
        self.assertTrue(self.agent.undo()['ok']);self.assertTrue(b.exists());self.assertFalse(a.exists())
        self.assertTrue(self.agent.undo()['ok']);self.assertTrue(a.exists());self.assertEqual(self.agent.undo()['code'],'nothing_to_undo')
    def test_undo_never_overwrites_recreated_original(self):
        p=self.file();pid=self.plan();self.runplan(pid);p.write_text('replacement')
        r=self.agent.undo();self.assertEqual(r['code'],'undo_partial');self.assertEqual(p.read_text(),'replacement');self.assertEqual((self.out/p.name).read_text(),'hello')
        p.unlink();self.assertTrue(self.agent.undo()['ok']);self.assertEqual(p.read_text(),'hello')
    def test_modified_output_not_reversed_or_deleted(self):
        p=self.file();pid=self.plan('copy');self.runplan(pid);(self.out/p.name).write_text('user edits')
        self.assertEqual(self.agent.undo()['code'],'undo_partial');self.assertEqual((self.out/p.name).read_text(),'user edits')
    def test_partial_failure_keeps_successful_operations_undoable(self):
        self.file('a.txt');self.file('b.txt');pid=self.plan();original=self.agent._publish
        def fail(op,action,**kwargs):
            if Path(op['from']).name=='b.txt':raise PermissionError('test failure')
            return original(op,action,**kwargs)
        with patch.object(self.agent,'_publish',side_effect=fail):r=self.agent.execute(pid,confirmed=True,turn=2)
        self.assertEqual(r['code'],'partial');self.assertTrue((self.out/'a.txt').exists());self.assertTrue((self.docs/'b.txt').exists())
        self.assertTrue(self.agent.undo()['ok']);self.assertTrue((self.docs/'a.txt').exists())
    def test_crash_after_move_before_completion_record_is_recoverable(self):
        p=self.file();pid=self.plan();state=self.agent._load();plan=state['plans'][pid];op=plan['ops'][0]
        plan['status']='running';op['status']='applying';state['batches'].append(pid);self.agent._save(state);self.agent._publish(op,'move')
        other=FileAgent(self.root,self.agent.state_dir);r=other.undo();self.assertTrue(r['ok'],r);self.assertTrue(p.exists())
    def test_crash_after_reverse_before_undone_record(self):
        p=self.file();pid=self.plan();self.runplan(pid);state=self.agent._load();op=state['plans'][pid]['ops'][0];op['status']='undoing';self.agent._save(state)
        self.agent._publish({'from':op['to'],'to':op['from'],'fingerprint':self.agent._fingerprint(op['to'])},'move')
        self.assertTrue(FileAgent(self.root,self.agent.state_dir).undo()['ok']);self.assertTrue(p.exists())
    def test_corrupt_state_fails_without_mutation(self):
        p=self.file();self.agent.state_dir.mkdir();self.agent.state_path.write_text('{invalid')
        self.assertFalse(self.agent.propose('move',destination=str(self.out))['ok']);self.assertTrue(p.exists())

    def test_replaced_copy_with_same_contents_is_not_deleted(self):
        self.file();pid=self.plan('copy');self.runplan(pid);dst=self.out/'note.txt'
        replacement=self.out/'replacement';replacement.write_text('hello');os.replace(replacement,dst)
        self.assertEqual(self.agent.undo()['code'],'undo_partial');self.assertTrue(dst.exists())
    def test_crash_after_link_before_source_removal(self):
        p=self.file();pid=self.plan();state=self.agent._load();plan=state['plans'][pid];op=plan['ops'][0]
        op['status']='applying';plan['status']='running';state['batches'].append(pid);self.out.mkdir();os.link(p,self.out/p.name);self.agent._save(state)
        self.assertTrue(self.agent.undo()['ok']);self.assertTrue(p.exists());self.assertFalse((self.out/p.name).exists())
    def test_recovery_never_deletes_an_unowned_identical_copy(self):
        p=self.file();pid=self.plan('copy');state=self.agent._load();plan=state['plans'][pid];op=plan['ops'][0]
        op['status']='applying';plan['status']='running';state['batches'].append(pid);self.file(text='hello',folder=self.out);self.agent._save(state)
        self.assertEqual(self.agent.undo()['code'],'undo_partial');self.assertTrue((self.out/p.name).exists())
    def test_valid_json_with_broken_shape_is_preserved(self):
        self.agent.state_dir.mkdir();self.agent.state_path.write_text('{"version":2}')
        self.assertEqual(self.agent.undo()['code'],'state_corrupt');self.assertEqual(self.agent.state_path.read_text(),'{"version":2}')

class RoutingTests(Fixture):
    def setUp(self):super().setUp();self.out.mkdir();self.file();self.session=FileSession(self.agent)
    def test_move_preview_approve_undo_workflow(self):
        r=self.session.handle('Move text files named "note.txt" from Documents to Archive',1);self.assertEqual(r['code'],'preview',r)
        self.assertEqual(self.session.handle('yes',2)['code'],'completed');self.assertTrue((self.out/'note.txt').exists())
        self.assertEqual(self.session.handle('undo',3)['code'],'undone');self.assertTrue((self.docs/'note.txt').exists())
    def test_cancel(self):
        self.session.handle('move "note.txt" to Archive',1);self.assertEqual(self.session.handle('cancel',2)['code'],'cancelled');self.assertTrue((self.docs/'note.txt').exists())
    def test_conditional_yes_does_not_approve(self):
        self.session.handle('move "note.txt" to Archive',1)
        self.assertEqual(self.session.handle('yes but only the PDFs',2)['code'],'clarification');self.assertTrue((self.docs/'note.txt').exists())
    def test_unrelated_topic_invalidates_generic_confirmation(self):
        self.session.handle('move "note.txt" to Archive',1);self.assertIsNone(self.session.handle('what is the weather?',2));self.assertIsNone(self.session.handle('yes',3));self.assertTrue((self.docs/'note.txt').exists())
        self.assertEqual(self.session.handle('show the last plan',4)['code'],'preview');self.assertEqual(self.session.handle('yes',5)['code'],'completed')
    def test_unknown_source_and_destination_do_not_guess(self):
        for text in ['move PDFs from TypoFolder to Archive','move PDFs from Documents to TypoFolder','move the files','move the files to Archive']:
            self.assertEqual(route(text,self.docs)[0],'clarify',text)
    def test_negative_and_information_request_do_not_move(self):
        self.assertEqual(route("don't move the files to Archive",self.docs)[0],'clarify')
        self.assertIsNone(route('how does a file move work?',self.docs))
    def test_quoted_paths_with_to_and_spaces(self):
        self.file('my to do.txt');(self.docs/'Work Reports').mkdir()
        args=parse('move "my to do.txt" from Documents to "Work Reports"',self.docs)
        self.assertEqual(args['pattern'],'my to do.txt');self.assertEqual(args['destination'],'Documents/Work Reports')
    def test_fuzzy_ambiguity_never_selects_first(self):
        (self.docs/'Work Reports').mkdir();(self.docs/'Work Report').mkdir();self.assertIsNone(match_dir('Work Repor',self.docs))
    def test_spoken_path_and_literal_name_preserved(self):
        self.assertIn('a_b.csv',norm('move a underscore b dot csv to Archive'))
        self.assertIn('"Dash Reports"',norm('move PDFs to "Dash Reports"'))
    def test_explicit_new_folder(self):
        args=parse('move "note.txt" to a new folder named Finished',self.docs);self.assertEqual(args['destination'],'Documents/Finished')
    def test_multiple_image_extensions(self):
        self.file('photo.JPG');self.file('photo.png');r=self.session.handle('copy images to Archive',1);self.assertEqual(len(r['data']['plan']['ops']),2)
    def test_directory_discovery_is_depth_and_entry_bounded(self):
        deep=self.docs/'one/two/three/four';deep.mkdir(parents=True);self.assertNotIn(Path('one/two/three'),_dirs(self.docs,depth=2));self.assertLessEqual(len(_dirs(self.docs,limit=2)),3)

class CandidateIntegrationTests(Fixture):
    def test_actual_candidate_file_turns_never_require_model_or_devices(self):
        import julius_files as jf
        from unittest.mock import Mock
        self.out.mkdir();p=self.file();turns=[]
        source=ast.parse(Path('julius_candidate.py').read_text())
        import julius_tools as jt
        from julius_intents import LiveIntentRouter
        from julius_utility_session import UtilitySession
        from julius_planning import Planner
        ns={'_utility_session':UtilitySession(Planner(Path(self.temp.name)/'planner')),'jt':jt,'intent_router':LiveIntentRouter(),'time':time,'jf':jf,'_file_session':FileSession(self.agent),'TURN':{'n':0},
            '_print_tool':Mock(),'eyes':Mock(),'jm':Mock(),'history':[],
            '_validation_output':str(Path(self.temp.name)/'validation.json'),'_validation_turns':turns,
            '_json':json,'Path':Path,'tts':Mock(backend='qwen-clone',worker_metrics={}),
            'close_backend':Mock(),'LLM_BACKEND':'ollama','LLM':'llama3.2:3b','speak':Mock(),'ask_streaming':Mock(side_effect=AssertionError('file request reached LLM'))}
        from julius_cancellation import Cancellation,TurnCancelled
        from julius_listening import incomplete_fragment
        from julius_conversation import authority_discussion_reply
        ns.update(handle_memory=Mock(return_value=None),memory_autocapture=Mock(),cancellation=Cancellation(),TurnCancelled=TurnCancelled,incomplete_fragment=incomplete_fragment,authority_discussion_reply=authority_discussion_reply)
        ns['eyes'].snapshot.return_value={};ns['eyes'].describe.return_value=''
        names={'handle_files','process','_process'}
        exec(compile(ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'<actual-candidate>','exec'),ns)
        with contextlib.redirect_stdout(io.StringIO()):
            for command in ['move "note.txt" to Archive','yes','undo']:ns['process'](command,'typed')
        self.assertEqual([r['file_result']['code'] for r in turns],['preview','completed','undone'])
        self.assertTrue(p.exists());ns['ask_streaming'].assert_not_called();self.assertEqual(ns['speak'].call_count,3)
    def test_model_read_tools_use_same_scoped_service(self):
        import julius_files as jf, julius_tools as tools
        p=self.file()
        with patch.object(jf,'agent',return_value=self.agent), patch.object(tools, '_dispatcher', None):
            self.assertIn('hello',tools.call('read_file',{'path':str(p)}))
            self.assertIn('note.txt',tools.call('find_files',{'pattern':'*.txt','source':'Documents'}))
            self.assertIn('outside',tools.call('read_file',{'path':str(self.root/'Library/private')}))
            self.assertIn('No tool',tools.call('execute_plan',{'plan_id':'invented'}))

class ExtendedWorkflowTests(Fixture):
    def setUp(self):super().setUp();self.out.mkdir();self.file();self.session=FileSession(self.agent)
    def test_read_list_find_commands_are_direct(self):
        self.assertEqual(self.session.handle('list files in Documents',1)['code'],'listed')
        self.assertEqual(self.session.handle('find "note.txt" in Documents',2)['code'],'found')
        self.assertEqual(self.session.handle('read "note.txt"',3)['data']['text'],'hello')
    def test_rename_command_and_undo(self):
        self.assertEqual(self.session.handle('rename "note.txt" to "new.txt"',1)['code'],'preview')
        self.assertEqual(self.session.handle('yes',2)['code'],'completed');self.assertTrue((self.docs/'new.txt').exists())
        self.assertEqual(self.session.handle('undo',3)['code'],'undone')
    def test_exact_text_creation_command(self):
        r=self.session.handle('create "new.txt" containing "Hello the user"',1);self.assertEqual(r['code'],'preview',r)
        self.assertEqual(self.session.handle('yes',2)['code'],'completed');self.assertEqual((self.docs/'new.txt').read_text(),'Hello the user')
    def test_copy_command_and_confirmation(self):
        self.assertEqual(self.session.handle('copy "note.txt" to Archive',1)['code'],'preview')
        self.assertEqual(self.session.handle('yes',2)['code'],'completed');self.assertTrue((self.docs/'note.txt').exists())
    def test_new_request_replaces_instead_of_approving_old_plan(self):
        a=self.session.handle('move "note.txt" to Archive',1);old=a['data']['plan']['id']
        self.assertEqual(self.session.handle('yes copy "note.txt" to Archive',2)['code'],'preview')
        self.assertEqual(self.agent.show(old)['data']['plan']['status'],'cancelled');self.assertTrue((self.docs/'note.txt').exists())
    def test_acknowledgement_with_unrelated_request_never_confirms(self):
        self.session.handle('move "note.txt" to Archive',1)
        self.assertIsNone(self.session.handle("okay what's the weather?",2));self.assertIsNone(self.session.handle('yes',3))
    def test_protected_history_excluded_from_user_operations(self):
        agent=FileAgent(self.root,self.docs/'Internal');agent.state_dir.mkdir();(agent.state_dir/'private.txt').write_text('history')
        self.assertEqual(agent.read(str(agent.state_dir/'private.txt'))['code'],'protected_path')
        self.assertNotIn('private.txt',[i['name'] for i in agent.find(recursive=True)['data']['items']])
    def test_folder_aliases_ignore_capitalization(self):
        self.assertTrue(self.agent.read('documents/note.txt')['ok'])
    def test_empty_allowed_roots_do_not_grant_default_access(self):
        other=FileAgent(self.root,self.agent.state_dir,allowed_roots=[])
        self.assertEqual(other.read('Documents/note.txt')['code'],'outside_scope')
    def test_concurrent_execution_runs_once(self):
        from concurrent.futures import ThreadPoolExecutor
        pid=self.plan()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(lambda _:self.agent.execute(pid,confirmed=True,turn=2),range(2)))
        self.assertEqual(sorted(r['code'] for r in outcomes),['already_processed','completed'])
    def test_crash_after_copy_publication_recovers_owned_output(self):
        pid=self.plan('copy');original=os.link
        def crash(source,target,**kwargs):
            original(source,target,**kwargs);raise KeyboardInterrupt('simulated restart')
        with patch('julius_file_agent.os.link',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):self.agent.execute(pid,confirmed=True,turn=2)
        self.assertTrue((self.out/'note.txt').exists());self.assertTrue(FileAgent(self.root,self.agent.state_dir).undo()['ok']);self.assertFalse((self.out/'note.txt').exists())
    def test_obviously_changed_size_is_rejected_without_hashing(self):
        p=self.file();expected=self.agent._fingerprint(p);p.write_text('much longer changed content')
        with patch.object(self.agent,'_fingerprint',side_effect=AssertionError('must not hash changed size')):
            self.assertFalse(self.agent._matches(p,expected,identity=True))

    def test_unclear_selected_files_are_not_expanded_to_all_of_a_type(self):
        for text in ['move those scripts to Archive','move this PDF to Archive','move a notebook to Archive']:
            self.assertEqual(route(text,self.docs)[0],'clarify',text)

    def test_pdf_is_not_misrepresented_as_extracted_document_text(self):
        p=self.file('report.pdf','%PDF-1.4 ASCII structure')
        self.assertEqual(self.agent.read(str(p))['code'],'unsupported_format')

    def test_polite_confirmation_is_accepted_without_loose_matching(self):
        self.session.handle('move "note.txt" to Archive',1)
        self.assertEqual(self.session.handle('yes, please.',2)['code'],'completed')

    def test_failed_move_cleanup_preserves_an_external_replacement(self):
        p=self.file();pid=self.plan();original=os.link
        def replace_after_publication(source,target,**kwargs):
            original(source,target,**kwargs)
            replacement=Path(target).with_name('app-replacement.tmp');replacement.write_text('another app')
            os.replace(replacement,target);Path(source).write_text('source edited')
        with patch('julius_file_agent.os.link',side_effect=replace_after_publication):
            self.assertEqual(self.agent.execute(pid,confirmed=True,turn=2)['code'],'partial')
        self.assertEqual((self.out/'note.txt').read_text(),'another app');self.assertEqual(p.read_text(),'source edited')
        self.assertEqual(self.agent.undo()['code'],'undo_partial');self.assertEqual((self.out/'note.txt').read_text(),'another app')

class ModelBoundaryTests(unittest.TestCase):
    def test_model_has_no_mutating_tools(self):
        import julius_tools as tools
        names={s['function']['name'] for s in tools.SCHEMAS}
        self.assertFalse(names & {'execute_plan','undo_files','propose_move'})
        self.assertNotIn('execute_plan',tools.REGISTRY)
        self.assertIn('find_files',names)

if __name__=='__main__':unittest.main()
