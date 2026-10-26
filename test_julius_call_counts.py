"""Model calls per turn type: a lookup that needs the model's summary is exactly two calls; shortcuts and ready sentences need fewer."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import julius_tools as jt
from julius_speech_text import speakable
from test_julius_candidate import load_candidate_turn

def tool_call(*calls):return SimpleNamespace(message=SimpleNamespace(content='',tool_calls=[SimpleNamespace(function=SimpleNamespace(name=n,arguments=a)) for n,a in calls]))
def text(t):return SimpleNamespace(message=SimpleNamespace(content=t,tool_calls=None))
def turn(prompt,responses,cloud=True,out='tool output',carry=False):
    """Run ask_streaming with scripted non-streaming responses; returns (model calls, spoken, result)."""
    ns=load_candidate_turn();ns['speakable']=speakable;ns['cloud_brain']=object() if cloud else None;ns['_tool_carry_next']=carry
    calls=[];spoken=[];queue=list(responses)
    def llm(**kw):
        calls.append(bool(kw.get('stream')))
        if kw.get('stream'):return iter([SimpleNamespace(message=SimpleNamespace(content='Spoken summary.'))])
        return queue.pop(0) if queue else text('')
    ns['llm_chat']=llm
    with patch.object(jt,'call',return_value=out):
        jt.dispatcher().last_result={'ok':True}
        result=ns['ask_streaming'](prompt,'',speak_fn=spoken.append)
    return calls,spoken,result
WEATHER='Springfield: 76°F, clear, feels like 76. High 77, low 50.'
class CallCountTests(unittest.TestCase):
    def test_router_shortcut_makes_no_model_call(self):
        calls,spoken,_=turn("What's 17% of 2480?",[]);self.assertEqual(calls,[])
        calls,_,_=turn("What's the weather in Springfield?",[],out=WEATHER);self.assertEqual(calls,[])
    def test_plain_chat_is_one_streamed_call(self):
        calls,spoken,_=turn('How are you doing today?',[]);self.assertEqual(calls,[True]);self.assertEqual(spoken,['Spoken summary.'])
    def test_simple_lookup_through_the_model_is_one_call_and_a_ready_sentence(self):
        for name,args,out in [('get_weather',{'location':'Springfield'},WEATHER),('get_quote',{'symbol':'ETH'},'Ethereum USD (ETH-USD): 2,309.82 USD, down 17.05 (0.73%) versus previous close.'),('calculate',{'expression':'2+2'},'2+2 = 4')]:
            calls,spoken,result=turn('Is it cold outside in Springfield?',[tool_call((name,args))],out=out)
            self.assertEqual(calls,[False],name);self.assertEqual(spoken,[result])
    def test_a_lookup_that_needs_the_model_summary_is_exactly_two_calls(self):
        calls,spoken,result=turn('List the files in my Downloads folder',[tool_call(('list_files',{'directory':'Downloads'}))],out='a.txt\nb.txt')
        self.assertEqual(calls,[False,True]);self.assertEqual(result,'Spoken summary.')
    def test_several_lookups_in_one_turn_are_still_exactly_two_calls(self):
        calls,_,_=turn('What is the weather in Springfield and in Tokyo?',[tool_call(('get_weather',{'location':'Springfield'}),('get_weather',{'location':'Tokyo'}))],out=WEATHER)
        self.assertEqual(calls,[False,True])
    def test_a_tool_turn_where_no_tool_is_needed_is_one_call_in_cloud_mode_and_two_locally(self):
        calls,spoken,_=turn('explain overfitting simply',[text('Overfitting is memorizing the training data. It fails on new data.')],cloud=True,carry=True)
        self.assertEqual(calls,[False]);self.assertEqual(len(spoken),2)
        calls,_,_=turn('explain overfitting simply',[text('tool-phase text')],cloud=False,carry=True);self.assertEqual(calls,[False,True])
    def test_a_tool_turn_never_exceeds_two_model_calls(self):
        endless=[tool_call(('list_files',{'directory':'x'})) for _ in range(5)]
        calls,_,_=turn('List the files in my Downloads folder',endless,out='a\nb');self.assertLessEqual(len(calls),2)
if __name__=='__main__':unittest.main()
