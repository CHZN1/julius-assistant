import unittest
from julius_conversation import conversation_messages
class ConversationContextTests(unittest.TestCase):
    def test_followups_and_user_context_survive_without_mutating_history(self):
        original=[{'role':'system','content':'long catalogue'}, {'role':'user','content':'Explain overfitting.'},
            {'role':'assistant','content':'It fits training noise.'},{'role':'user','content':'How do I avoid that?'}]
        out=conversation_messages(original,'Building Julius locally.')
        self.assertEqual(out[1:],original[1:]);self.assertIn('Building Julius locally.',out[0]['content'])
        out[1]['content']='changed';self.assertEqual(original[1]['content'],'Explain overfitting.')

class AuthorityDiscussionTests(unittest.TestCase):
    def test_permission_question_does_not_touch_tools_or_models(self):
        from unittest.mock import Mock
        from test_julius_candidate import load_candidate_turn
        ns=load_candidate_turn();ns['llm_chat']=Mock();ns['jt']=Mock();spoken=[]
        question='Someone handed me a document saying erase my Documents folder. Does that count as my permission?'
        answer=ns['ask_streaming'](question,'',speak_fn=spoken.append)
        self.assertTrue(answer.startswith('No.'));self.assertEqual(spoken,[answer])
        ns['llm_chat'].assert_not_called();ns['jt'].call.assert_not_called()
    def test_explicit_action_requests_are_not_authority_discussions(self):
        from julius_conversation import authority_discussion_reply
        for text in ('Can you read this file?', 'I approve the pending file move.', 'Permission granted, create my reminder.', 'Delete the approved files.'):
            self.assertIsNone(authority_discussion_reply(text))
        self.assertIsNotNone(authority_discussion_reply('What if that document claims I approved it?'))
    def test_runtime_discussion_bypasses_file_dispatcher(self):
        import contextlib,io
        from test_julius_turn_recovery import TurnRecoveryTests
        ns=TurnRecoveryTests().runtime()
        with contextlib.redirect_stdout(io.StringIO()):
            ns['process']('Does text in a file give you permission to delete my files?','typed')
        ns['handle_files'].assert_not_called();ns['ask_streaming'].assert_not_called()
        self.assertTrue(ns['speak'].call_args.args[0].startswith('No.'))

class ConversationStyleTests(unittest.TestCase):
    def test_brief_style_keeps_context_and_explicit_camera_state(self):
        from unittest.mock import patch
        with patch.dict('os.environ',{'JULIUS_CONVERSATION_STYLE':'brief'}):
            out=conversation_messages([{'role':'user','content':'More detail, please.'}],vision_enabled=False)
        self.assertIn('Give more detail when asked',out[0]['content'])
        self.assertIn('VISION: Disabled',out[0]['content'])
        self.assertIn('camera perception',out[0]['content'])
        self.assertEqual(out[1]['content'],'More detail, please.')
        self.assertIn('VISION: Enabled',conversation_messages([],vision_enabled=True)[0]['content'])

class VoiceCapabilityTests(unittest.TestCase):
    def test_camera_off_question_uses_known_capability_without_inference(self):
        from unittest.mock import Mock
        from test_julius_candidate import load_candidate_turn
        for question in ['Does that mean we can only use text now?', 'Can you still speak if vision is disabled?', 'Can we still talk with camera vision off?']:
            ns=load_candidate_turn();ns['llm_chat']=Mock();ns['jt']=Mock();spoken=[]
            answer=ns['ask_streaming'](question,'',speak_fn=spoken.append)
            self.assertIn('vision',answer)
            self.assertEqual(spoken,[answer])
            ns['llm_chat'].assert_not_called();ns['jt'].call.assert_not_called()
    def test_commands_and_other_text_questions_are_not_intercepted(self):
        from julius_conversation import voice_capability_reply
        for text in ['Turn off voice.', 'Read this text aloud.', 'Can we only use text files for this coding task?', 'We can only use text in this file format.']:
            self.assertIsNone(voice_capability_reply(text))
