"""Sonnet planner, Haiku sub-agents and the propose-only file agent, with a scripted fake API."""
import atexit, shutil, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import anthropic, httpx
import julius_cloud as jc, julius_config as cfg, julius_costs as costs, julius_orchestrator as orch, julius_tools as jt
from julius_dispatcher import Dispatcher
from julius_file_agent import FileAgent
from julius_file_session import FileSession

REQ=httpx.Request('POST','https://api.anthropic.com/v1/messages')
def usage(i=200,o=50,r=0):return SimpleNamespace(input_tokens=i,output_tokens=o,cache_creation_input_tokens=0,cache_read_input_tokens=r)
def text(t):return SimpleNamespace(type='text',text=t)
def use(i,name,**inp):return SimpleNamespace(type='tool_use',id=i,name=name,input=inp)
def think():return SimpleNamespace(type='thinking',thinking='',signature='sig')
def resp(blocks,stop='end_turn',u=None):return SimpleNamespace(content=blocks,stop_reason=stop,usage=u or usage())
class Scripted:
    def __init__(self,*responses):self.queue=list(responses);self.calls=[];self.messages=self
    def create(self,**kw):
        self.calls.append({**kw,'messages':[dict(m) for m in kw['messages']]})
        item=self.queue.pop(0)
        if isinstance(item,Exception):raise item
        return item
def make(*responses,cap=5.0):
    d=tempfile.mkdtemp();atexit.register(shutil.rmtree,d,True)
    tracker=costs.CostTracker(Path(d)/'c.jsonl',cap);api=Scripted(*responses)
    brain=jc.CloudBrain(cfg.load_config(),tracker,client=SimpleNamespace(messages=api));brain._slow=SimpleNamespace(messages=api)
    return brain,api

class PlannerTests(unittest.TestCase):
    def test_direct_answer_uses_sonnet_with_caching_and_no_sampling_params(self):
        brain,api=make(resp([text('Sure. It is done.')]))
        self.assertEqual(orch.run_escalation({'task':'plan my week'},None,brain=brain),'Sure. It is done.')
        kw=api.calls[0];self.assertEqual(kw['model'],'claude-sonnet-5-5')
        for banned in ('temperature','top_p','top_k'):self.assertNotIn(banned,kw)
        self.assertNotIn('thinking',kw);self.assertEqual(kw['output_config'],{'effort':'medium'})
        self.assertEqual(kw['cache_control'],{'type':'ephemeral'});self.assertEqual(kw['system'][0]['cache_control'],{'type':'ephemeral'})
        self.assertEqual(kw['tools'][-1]['cache_control'],{'type':'ephemeral'});self.assertEqual(brain.tracker.today_by_tier().keys(),{'sonnet'})
    def test_empty_task_falls_back_to_the_users_own_words(self):
        brain,api=make(resp([text('Planned.')]));orch.configure(user_text='Plan a trip to Tokyo');self.addCleanup(lambda:orch.configure(user_text=''))
        self.assertEqual(orch.run_escalation({},None,brain=brain),'Planned.');self.assertEqual(api.calls[0]['messages'][0]['content'],'Plan a trip to Tokyo')
    def test_the_users_exact_words_are_appended_to_a_paraphrased_task(self):
        brain,api=make(resp([text('ok')]));orch.configure(user_text='Organize my downloads by file type, keep PDFs on top');self.addCleanup(lambda:orch.configure(user_text=''))
        orch.run_escalation({'task':'Sort the Downloads folder'},None,brain=brain);sent=api.calls[0]['messages'][0]['content']
        self.assertIn('Sort the Downloads folder',sent);self.assertIn('keep PDFs on top',sent)
    def test_planner_tools_are_utilities_plus_delegate_and_propose_only_files(self):
        names={t['name'] for t in orch.Planner(make()[0]).tools()}
        self.assertTrue({'delegate','files_propose','calculate','get_weather','list_files'}<=names)
        self.assertFalse({'execute_plan','escalate_to_planner','run_code'}&names)
    def test_delegate_runs_a_haiku_subagent_and_planner_checks_its_result(self):
        brain,api=make(
            resp([think(),use('p1','delegate',system_prompt='You research weather.',task='Weather in Springfield and Tokyo',tools=['get_weather'])],'tool_use'),
            resp([use('s1','get_weather',location='Springfield')],'tool_use',usage(i=300,o=30)),
            resp([text('Springfield 75F.')],'end_turn',usage(i=320,o=12)),
            resp([text('Springfield is 75 degrees.')]))
        with patch.object(jt,'call',return_value='Springfield: 75F') as call:
            out=orch.run_escalation({'task':'weather compare'},None,brain=brain)
        self.assertEqual(out,'Springfield is 75 degrees.');call.assert_called_once_with('get_weather',{'location':'Springfield'})
        self.assertEqual([c['model'] for c in api.calls],['claude-sonnet-5-5','claude-haiku-5-5','claude-haiku-5-5','claude-sonnet-5-5'])
        sub=api.calls[1];self.assertEqual({t['name'] for t in sub['tools']},{'get_weather'});self.assertIn('You research weather.',sub['system'][0]['text'])
        self.assertIn('sub-agent',sub['system'][0]['text']);self.assertEqual(sub['thinking'],{'type':'disabled'})
        planner_followup=api.calls[3]['messages'];self.assertEqual(planner_followup[1]['content'][0].type,'thinking')  # thinking returned unchanged
        result=planner_followup[-1]['content'][0];self.assertEqual((result['type'],result['tool_use_id']),('tool_result','p1'));self.assertIn('Springfield 75F.',result['content'])
        self.assertEqual(brain.tracker.today_by_tier().keys(),{'sonnet','haiku'})
    def test_subagent_cannot_use_tools_it_was_not_given(self):
        brain,api=make(resp([use('p1','delegate',system_prompt='x',task='y',tools=['calculate'])],'tool_use'),
                       resp([use('s1','get_weather',location='X')],'tool_use'),resp([text('ok')]),resp([text('done')]))
        with patch.object(jt,'call') as call:orch.run_escalation({'task':'t'},None,brain=brain)
        call.assert_not_called();self.assertIn('not available to this sub-agent',api.calls[2]['messages'][-1]['content'][0]['content'])
    def test_subagent_tool_list_excludes_proposals_delegation_and_escalation(self):
        self.assertFalse({'files_propose','delegate','escalate_to_planner'}&set(orch.SUBAGENT_TOOLS))
        enum=orch.DELEGATE_SCHEMA['input_schema']['properties']['tools']['items']['enum'];self.assertEqual(tuple(enum),orch.SUBAGENT_TOOLS)
    def test_planner_step_limit_forces_a_summary_without_tools(self):
        loop=[resp([use(f'p{i}','calculate',expression='1+1')],'tool_use') for i in range(8)]
        brain,api=make(*loop,resp([text('I did eight steps and stopped.')]))
        with patch.object(jt,'call',return_value='2'):out=orch.run_escalation({'task':'loop forever'},None,brain=brain)
        self.assertEqual(out,'I did eight steps and stopped.');self.assertEqual(len(api.calls),9);self.assertEqual(api.calls[-1]['tool_choice'],{'type':'none'})
        self.assertIn('Step limit reached',api.calls[-1]['messages'][-1]['content'][-1]['text'])
    def test_subagent_step_limit_is_capped_by_config(self):
        brain,api=make(resp([use('p1','delegate',system_prompt='x',task='y',tools=['calculate'],max_steps=50)],'tool_use'),
                       *[resp([use(f's{i}','calculate',expression='1')],'tool_use') for i in range(6)],resp([text('stopped')]),resp([text('Final.')]))
        with patch.object(jt,'call',return_value='1'):orch.run_escalation({'task':'t'},None,brain=brain)
        haiku=[c for c in api.calls if c['model']=='claude-haiku-5-5'];self.assertEqual(len(haiku),7)  # 6 steps + the forced summary
        self.assertEqual(haiku[-1]['tool_choice'],{'type':'none'})
    def test_tool_errors_are_returned_to_the_model_as_errors(self):
        brain,api=make(resp([use('p1','calculate',expression='1/0')],'tool_use'),resp([text('That failed.')]))
        with patch.object(jt,'call',side_effect=ValueError('bad')):orch.run_escalation({'task':'t'},None,brain=brain)
        res=api.calls[1]['messages'][-1]['content'][0];self.assertTrue(res['is_error']);self.assertIn('bad',res['content'])
    def test_summary_is_made_speakable(self):
        brain,_=make(resp([text('**Done**\n\n- one\n- two '+'x '*400)]))
        out=orch.run_escalation({'task':'t'},None,brain=brain);self.assertNotIn('*',out);self.assertNotIn('\n',out);self.assertLessEqual(len(out),orch.SPOKEN_LIMIT+1)

class SafetyTests(unittest.TestCase):
    def test_cap_reached_blocks_escalation_before_any_call(self):
        brain,api=make(cap=0.0000001);brain.tracker.record('sonnet',usage(o=1000))
        self.assertIn('spending limit',orch.run_escalation({'task':'t'},None,brain=brain));self.assertEqual(api.calls,[])
    def test_cap_reached_mid_run_stops_the_planner(self):
        brain,api=make(resp([use('p1','calculate',expression='1')],'tool_use',usage(i=10,o=500_000)),cap=1.0)
        with patch.object(jt,'call',return_value='1'):out=orch.run_escalation({'task':'t'},None,brain=brain)
        self.assertIn('spending limit',out);self.assertEqual(len(api.calls),1)
    def test_auth_error_is_spoken(self):
        err=anthropic.AuthenticationError('bad key',response=httpx.Response(401,request=REQ),body=None)
        brain,_=make(err);self.assertEqual(orch.run_escalation({'task':'t'},None,brain=brain),'My API key expired or is invalid.');self.assertTrue(brain.auth_bad)
        self.assertEqual(orch.run_escalation({'task':'t'},None,brain=brain),'My API key expired or is invalid.')
    def test_connection_error_is_reported_not_raised(self):
        brain,_=make(anthropic.APIConnectionError(request=REQ));self.assertIn("couldn't reach",orch.run_escalation({'task':'t'},None,brain=brain))
    def test_empty_task_is_rejected_without_a_call(self):
        brain,api=make();self.assertIn('what to plan',orch.run_escalation({'task':' '},None,brain=brain));self.assertEqual(api.calls,[])
    def test_dispatcher_limit_is_raised_during_the_run_and_restored_and_task_recorded(self):
        d=Dispatcher({},[]);brain,_=make(resp([text('ok')]));seen=[]
        orig=orch.Planner.run
        def spy(self,task):seen.append(d.turn_call_limit);return orig(self,task)
        with patch.object(orch.Planner,'run',spy):orch.run_escalation({'task':'t'},d,brain=brain)
        self.assertEqual((seen,d.turn_call_limit),([60],8));self.assertEqual((d.last_result['tool'],d.last_result['ok'],d.last_result['agent']),('escalate_to_planner',True,'planner'))
    def test_jt_call_routes_escalation_outside_the_dispatcher_lock(self):
        with patch.object(orch,'run_escalation',return_value='spoken') as run:self.assertEqual(jt.call('escalate_to_planner',{'task':'x'}),'spoken')
        run.assert_called_once()

class FileProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=(Path(self.temp.name)/'home').resolve();self.dl=self.root/'Downloads';self.dl.mkdir(parents=True);(self.root/'Documents').mkdir();(self.root/'Desktop').mkdir()
        (self.dl/'a.csv').write_text('1');(self.dl/'b.csv').write_text('2')
        self.agent=FileAgent(self.root,Path(self.temp.name)/'state');self.session=FileSession(self.agent)
        orch.configure(file_session=self.session,turn=lambda:7);self.addCleanup(lambda:orch.configure(file_session=None))
    def test_proposal_is_staged_for_the_human_and_changes_nothing(self):
        out=orch.files_propose({'action':'move','source':'Downloads','destination':'Documents/Data','pattern':'*.csv'})
        self.assertIn('2 file(s)',out);self.assertIn('Staged only',out);self.assertTrue((self.dl/'a.csv').exists());self.assertFalse((self.root/'Documents'/'Data').exists())
        self.assertIsNotNone(self.session.pending);self.assertEqual(self.session.pending,self.session.last)
    def test_the_users_yes_executes_the_staged_plan_through_the_existing_human_path(self):
        orch.files_propose({'action':'move','source':'Downloads','destination':'Documents/Data','pattern':'*.csv'})
        out=self.session.handle('yes',8);self.assertTrue(out['ok']);self.assertTrue((self.root/'Documents'/'Data'/'a.csv').exists());self.assertFalse((self.dl/'a.csv').exists())
    def test_cancel_discards_and_a_new_proposal_replaces_the_pending_one(self):
        orch.files_propose({'action':'move','source':'Downloads','destination':'Documents/Data','pattern':'*.csv'});first=self.session.pending
        orch.files_propose({'action':'create','path':'Documents/note.txt','content':'hi'});self.assertNotEqual(self.session.pending,first)
        self.session.handle('cancel',9);self.assertFalse((self.root/'Documents'/'note.txt').exists());self.assertTrue((self.dl/'b.csv').exists())
    def test_rename_and_failures_are_reported_plainly(self):
        self.assertIn('rename',orch.files_propose({'action':'rename','path':'Downloads/a.csv','destination':'Downloads/z.csv'}))
        self.assertIn('Nothing will change',orch.files_propose({'action':'move','source':'Downloads','destination':'Documents/D','pattern':'*.mp3'}))
        self.assertIn('Which folder',orch.files_propose({'action':'move','source':'Downloads','destination':'','pattern':'*.csv'}))
    def test_without_a_file_session_nothing_is_proposed(self):
        orch.configure(file_session=None);orch._STATE['file_session']=None;self.assertIn('not available',orch.files_propose({'action':'move','destination':'x'}))
if __name__=='__main__':unittest.main()
