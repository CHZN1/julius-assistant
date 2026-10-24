"""Haiku front end: message/tool conversion, caching, streaming, cost recording, offline and auth handling."""
import atexit, shutil, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
import anthropic, httpx
import julius_cloud as jc, julius_config as cfg, julius_costs as costs
import julius_tools as jt

REQ=httpx.Request('POST','https://api.anthropic.com/v1/messages')
def auth_error():return anthropic.AuthenticationError('invalid x-api-key',response=httpx.Response(401,request=REQ),body=None)
def conn_error():return anthropic.APIConnectionError(request=REQ)
def usage(i=100,o=20,w=0,r=0):return SimpleNamespace(input_tokens=i,output_tokens=o,cache_creation_input_tokens=w,cache_read_input_tokens=r)
def block(kind,**kw):return SimpleNamespace(type=kind,**kw)

class FakeStream:
    def __init__(self,texts,u,boom=None):self.texts,self.u,self.boom=texts,u,boom;self.current_message_snapshot=SimpleNamespace(usage=u)
    def __enter__(self):return self
    def __exit__(self,*a):return False
    @property
    def text_stream(self):
        if self.boom:raise self.boom
        yield from self.texts
class FakeMessages:
    def __init__(self,response=None,stream=None,error=None):self.response,self._stream,self.error,self.calls=response,stream,error,[]
    def create(self,**kw):
        self.calls.append(kw)
        if self.error:raise self.error
        return self.response
    def stream(self,**kw):
        self.calls.append(kw)
        if self.error:raise self.error
        return self._stream
def brain(messages_api,offline=None,cap=5.0):
    d=tempfile.mkdtemp();atexit.register(shutil.rmtree,d,True);t=costs.CostTracker(Path(d)/'c.jsonl',cap)
    b=jc.CloudBrain(cfg.load_config(),t,client=SimpleNamespace(messages=messages_api),offline=offline)
    b._dir=d;return b
MSGS=[{'role':'system','content':'You are Julius.'},{'role':'user','content':'hi'}]

class ConversionTests(unittest.TestCase):
 def test_system_and_roles(self):
  system,out=jc.convert_messages(MSGS+[{'role':'assistant','content':'hello'},{'role':'user','content':'more'}])
  self.assertEqual(system,'You are Julius.');self.assertEqual([m['role'] for m in out],['user','assistant','user'])
 def test_tool_calls_and_results_pair_up_in_order(self):
  _,out=jc.convert_messages(MSGS+[{'role':'assistant','content':'','tool_calls':[{'function':{'name':'a','arguments':{'x':1}}},{'function':{'name':'b','arguments':{}}}]},
   {'role':'tool','name':'a','content':'ra'},{'role':'tool','name':'b','content':'rb'}])
  use=[b for b in out[1]['content'] if b['type']=='tool_use'];res=out[2]['content']
  self.assertEqual([r['tool_use_id'] for r in res],[u['id'] for u in use]);self.assertEqual(use[0]['input'],{'x':1})
  self.assertTrue(all(r['type']=='tool_result' for r in res))
 def test_late_system_note_rides_with_latest_user_turn(self):
  _,out=jc.convert_messages(MSGS+[{'role':'assistant','content':'','tool_calls':[{'function':{'name':'a','arguments':{}}}]},{'role':'tool','name':'a','content':'r'},{'role':'system','content':'Be brief.'}])
  self.assertEqual(out[-1]['role'],'user');self.assertEqual(out[-1]['content'][-1],{'type':'text','text':'Be brief.'})
 def test_tools_convert_and_last_one_is_cached(self):
  tools=jc.convert_tools(jt.SCHEMAS[:3]);self.assertEqual(tools[0]['name'],jt.SCHEMAS[0]['function']['name']);self.assertIn('input_schema',tools[0])
  self.assertNotIn('cache_control',tools[0]);self.assertEqual(tools[-1]['cache_control'],{'type':'ephemeral'})

class RequestTests(unittest.TestCase):
 def test_request_uses_haiku_55_with_caching_and_no_sampling_params(self):
  b=brain(FakeMessages());kw=b.request(MSGS,jt.SCHEMAS)
  self.assertEqual(kw['model'],'claude-haiku-5-5')
  for banned in ('temperature','top_p','top_k'):self.assertNotIn(banned,kw)
  self.assertEqual(kw['cache_control'],{'type':'ephemeral'});self.assertEqual(kw['system'][0]['cache_control'],{'type':'ephemeral'})
  self.assertEqual(kw['tools'][-1]['cache_control'],{'type':'ephemeral'});self.assertEqual(kw['output_config'],{'effort':'low'})
  self.assertEqual(kw['thinking'],{'type':'disabled'})
 def test_tools_are_selective_per_turn_and_always_include_escalation(self):
  b=brain(FakeMessages());names=lambda kw:[t['name'] for t in kw['tools']]
  weather=names(b.request(MSGS,jt.select_schemas('weather')));self.assertEqual(weather,['get_weather','get_forecast','escalate_to_planner'])
  self.assertEqual(names(b.request(MSGS,jt.select_schemas('bitcoin'))),['get_quote','escalate_to_planner'])
  self.assertLess(len(names(b.request(MSGS,jt.select_schemas('weather')))),len(jt.SCHEMAS))
 def test_each_tool_subset_is_cache_marked_on_its_last_tool(self):
  kw=brain(FakeMessages()).request(MSGS,jt.select_schemas('weather'));self.assertEqual(kw['tools'][-1]['cache_control'],{'type':'ephemeral'});self.assertNotIn('cache_control',kw['tools'][0])
 def test_follow_up_after_tools_reuses_the_same_tool_list_for_a_cache_hit(self):
  b=brain(FakeMessages());first=b.request(MSGS,jt.select_schemas('weather'))
  history=MSGS+[{'role':'assistant','content':'','tool_calls':[{'function':{'name':'get_weather','arguments':{}}}]},{'role':'tool','name':'get_weather','content':'75F'}]
  second=b.request(history,None);self.assertEqual(second['tools'],first['tools']);self.assertEqual(second['tool_choice'],{'type':'none'})
 def test_escalation_is_withdrawn_when_cap_is_reached(self):
  b=brain(FakeMessages(),cap=0.0000001);b.tracker.record('sonnet',usage(o=1000))
  self.assertNotIn('escalate_to_planner',[t['name'] for t in b.request(MSGS,jt.SCHEMAS)['tools']])
 def test_history_with_tool_calls_declares_tools_but_forbids_calling_them(self):
  history=MSGS+[{'role':'assistant','content':'','tool_calls':[{'function':{'name':'calculate','arguments':{'expression':'1+1'}}}]},{'role':'tool','name':'calculate','content':'2'}]
  kw=brain(FakeMessages()).request(history,None);self.assertEqual(kw['tool_choice'],{'type':'none'});self.assertIn('tools',kw)
 def test_plain_chat_sends_no_tools(self):
  self.assertNotIn('tools',brain(FakeMessages()).request(MSGS,None))

class ResponseTests(unittest.TestCase):
 def test_tool_call_response_is_shaped_like_ollama_and_cost_recorded(self):
  resp=SimpleNamespace(content=[block('text',text=''),block('tool_use',name='get_weather',input={'location':'Springfield'},id='t1')],usage=usage(i=50,o=10,r=1500))
  b=brain(FakeMessages(response=resp));r=b.chat(MSGS,tools=jt.SCHEMAS)
  self.assertEqual((r.message.tool_calls[0].function.name,r.message.tool_calls[0].function.arguments),('get_weather',{'location':'Springfield'}))
  self.assertAlmostEqual(b.tracker.today_total(),costs.cost_of('haiku',usage(i=50,o=10,r=1500)));self.assertEqual(b.tracker.today_by_tier().keys(),{'haiku'})
 def test_streaming_yields_text_parts_and_records_usage(self):
  fs=FakeStream(['Hello. ','There.'],usage(i=40,o=6,r=1400));b=brain(FakeMessages(stream=fs))
  self.assertEqual(''.join(p.message.content for p in b.chat(MSGS,stream=True)),'Hello. There.')
  self.assertEqual(b.tracker.today_by_tier().keys(),{'haiku'});self.assertGreater(b.tracker.today_total(),0)
 def test_closing_a_stream_early_still_records_usage(self):
  b=brain(FakeMessages(stream=FakeStream(['a','b','c'],usage())));gen=b.chat(MSGS,stream=True);next(gen);gen.close()
  self.assertGreater(b.tracker.today_total(),0)

class FailureTests(unittest.TestCase):
 def test_auth_error_is_spoken_not_hidden_and_disables_cloud(self):
  b=brain(FakeMessages(error=auth_error()),offline=lambda *a:self.fail('must not fall back silently'))
  r=b.chat(MSGS);self.assertEqual(r.message.content,'My API key expired or is invalid.');self.assertTrue(b.auth_bad)
  self.assertEqual(''.join(p.message.content for p in brain(FakeMessages(error=auth_error())).chat(MSGS,stream=True)),'My API key expired or is invalid.')
 def test_auth_error_mid_stream_is_spoken(self):
  b=brain(FakeMessages(stream=FakeStream([],usage(),boom=auth_error())))
  self.assertEqual(''.join(p.message.content for p in b.chat(MSGS,stream=True)),'My API key expired or is invalid.');self.assertTrue(b.auth_bad)
 def test_missing_key_is_spoken_the_same_way(self):
  b=brain(FakeMessages());b._client=None
  import unittest.mock as m
  with m.patch.object(jc,'make_client',side_effect=jc.MissingKey()):self.assertEqual(b.chat(MSGS).message.content,jc.AUTH_MESSAGE)
 def test_connection_error_uses_offline_model_and_stays_offline_for_the_retry_window(self):
  calls=[];api=FakeMessages(error=conn_error());now=[0.0]
  b=brain(api,offline=lambda m,t,s,o:calls.append(1) or SimpleNamespace(message=SimpleNamespace(content='local',tool_calls=None)));b._clock=lambda:now[0]
  self.assertEqual(b.chat(MSGS).message.content,'local');n=len(api.calls);b.chat(MSGS);self.assertEqual(len(api.calls),n)  # no API attempt while offline
  now[0]+=b.config['offline']['retry_seconds']+1;b.chat(MSGS);self.assertEqual(len(api.calls),n+1)
 def test_stream_connection_error_before_first_token_falls_back_offline(self):
  b=brain(FakeMessages(stream=FakeStream([],usage(),boom=conn_error())),offline=lambda m,t,s,o:iter([SimpleNamespace(message=SimpleNamespace(content='local reply'))]))
  self.assertEqual(''.join(p.message.content for p in b.chat(MSGS,stream=True)),'local reply')
 def test_stream_error_after_speaking_ends_quietly_without_repeating(self):
  class Half(FakeStream):
   @property
   def text_stream(self):
    yield 'Partial. ';raise conn_error()
  b=brain(FakeMessages(stream=Half([],usage())),offline=lambda *a:self.fail('no restart after partial speech'))
  self.assertEqual(''.join(p.message.content for p in b.chat(MSGS,stream=True)),'Partial. ')
 def test_cap_then_double_cap_notices_are_each_given_once(self):
  b=brain(FakeMessages(),cap=0.01);self.assertIsNone(b.cap_notice());b.tracker.record('sonnet',usage(o=1500))  # $0.015: over the cap, under twice the cap
  self.assertEqual(b.cap_notice(),jc.CAP_MESSAGE);self.assertIsNone(b.cap_notice())
  b.tracker.record('sonnet',usage(o=1500));self.assertEqual(b.cap_notice(),jc.HARD_CAP_MESSAGE);self.assertIsNone(b.cap_notice())
 def test_jumping_straight_past_double_the_cap_announces_the_offline_switch_only_once(self):
  b=brain(FakeMessages(),cap=0.001);b.tracker.record('sonnet',usage(o=5000))
  self.assertEqual(b.cap_notice(),jc.HARD_CAP_MESSAGE);self.assertIsNone(b.cap_notice())
 def test_at_double_the_cap_the_api_is_not_called_and_the_offline_model_answers(self):
  api=FakeMessages(response=SimpleNamespace(content=[block('text',text='cloud')],usage=usage()))
  b=brain(api,offline=lambda m,t,s,o:SimpleNamespace(message=SimpleNamespace(content='offline',tool_calls=None)),cap=0.001);b.tracker.record('sonnet',usage(o=5000))
  self.assertEqual(b.chat(MSGS).message.content,'offline');self.assertEqual(api.calls,[])
 def test_between_cap_and_double_cap_haiku_still_answers_without_escalation(self):
  api=FakeMessages(response=SimpleNamespace(content=[block('text',text='cloud')],usage=usage()))
  b=brain(api,cap=0.01);b.tracker.record('sonnet',usage(o=1500))
  self.assertEqual(b.chat(MSGS,tools=jt.select_schemas('weather')).message.content,'cloud')
  self.assertNotIn('escalate_to_planner',[t['name'] for t in api.calls[0]['tools']])

class BackendWiringTests(unittest.TestCase):
 def test_llm_chat_routes_to_the_cloud_brain_and_hands_it_the_offline_fallback(self):
  import julius_backend as be
  from unittest.mock import patch,MagicMock
  fake=MagicMock();fake.chat.return_value='answer'
  with patch.object(be,'LLM_BACKEND','cloud'),patch.object(jc,'get_brain',return_value=fake) as g:
   self.assertEqual(be.llm_chat(messages=MSGS,stream=False),'answer');g.assert_called_once_with(offline=be._offline_chat)
 def test_offline_model_is_loaded_only_on_demand_and_released_with_the_cloud_backend(self):
  import julius_backend as be
  from unittest.mock import patch
  with patch('ollama.chat',return_value='local') as chat,patch('ollama.generate') as gen,patch.object(be,'LLM_BACKEND','cloud'):
   be._offline_chat(MSGS,[ESC:=jc.ESCALATE_SCHEMA]+jt.SCHEMAS[:1],False,None)
   self.assertEqual(chat.call_args.kwargs['model'],'llama3.2:3b');self.assertEqual(chat.call_args.kwargs['keep_alive'],'2m')
   self.assertNotIn('escalate_to_planner',[t['function']['name'] for t in chat.call_args.kwargs['tools']])
   be.close_backend();gen.assert_called_once_with(model='llama3.2:3b',prompt='',keep_alive=0);be.close_backend();gen.assert_called_once()
 def test_cloud_backend_never_keeps_a_local_brain_resident(self):
  import julius_backend as be
  from unittest.mock import patch
  with patch.object(be,'LLM_BACKEND','cloud'):self.assertFalse(be.keep_brain_loaded())
class AuthDeliveryTests(unittest.TestCase):
 def says(self,gen):return ''.join(p.message.content for p in gen)
 def test_tool_phase_auth_error_is_delivered_on_the_following_streamed_reply_then_offline(self):
  local=lambda m,t,s,o:iter([SimpleNamespace(message=SimpleNamespace(content='local'))])
  b=brain(FakeMessages(error=auth_error()),offline=local)
  first=b.chat(MSGS,tools=jt.SCHEMAS);self.assertIsNone(first.message.tool_calls);self.assertTrue(b.auth_pending)
  self.assertEqual(self.says(b.chat(MSGS,stream=True)),jc.AUTH_MESSAGE)            # spoken on the reply the user hears
  self.assertFalse(b.auth_pending);self.assertEqual(self.says(b.chat(MSGS,stream=True)),'local')  # afterwards: announced offline model
 def test_planner_auth_failure_is_spoken_by_the_next_streamed_reply(self):
  import julius_orchestrator as orch
  b=brain(FakeMessages(),offline=lambda *a:self.fail('silent fallback'));b.mark_auth_bad()
  self.assertEqual(self.says(b.chat(MSGS,stream=True)),jc.AUTH_MESSAGE)
if __name__=='__main__':unittest.main()
