"""Text for the ear (symbols to words), ready-to-speak tool sentences, and the one-round-trip tool policy."""
import ast, os, re, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import julius_tools as jt
from julius_speech_text import normalize_for_speech as n, speakable, SPOKEN_STYLE_PROMPT, TOOL_POLICY_PROMPT, EXACT_REQUEST
from test_julius_candidate import load_candidate_turn

class NormalizeTests(unittest.TestCase):
    def test_degrees_percent_and_money(self):
        self.assertEqual(n("It's 77°F and clear."),"It's 77 degrees and clear.")
        self.assertEqual(n('-5°C tonight'),'minus 5 degrees Celsius tonight')
        self.assertEqual(n('17% of 2,480'),'17 percent of 2,480')
        self.assertEqual(n('$2,309.82'),'2,309 dollars and 82 cents');self.assertEqual(n('$5'),'5 dollars');self.assertEqual(n('$1'),'1 dollar')
        self.assertEqual(n('$0.50'),'50 cents');self.assertEqual(n('$1.5 million'),'1 point 5 million dollars')
    def test_ranges_decimals_negatives(self):
        self.assertEqual(n('51–71°F tomorrow'),'51 to 71 degrees tomorrow');self.assertEqual(n('51-71'),'51 to 71')
        self.assertEqual(n('421.6'),'421 point 6');self.assertEqual(n('down 0.73%'),'down 0 point 7 3 percent');self.assertEqual(n('it is -3 outside'),'it is minus 3 outside')
    def test_times_and_dates(self):
        self.assertEqual(n('at 3:45 PM'),'at three forty-five P M');self.assertEqual(n('9:05 AM'),'nine oh five A M');self.assertEqual(n('3:00 PM'),'three P M')
        self.assertEqual(n('3:45 p.m.'),'three forty-five P M');self.assertEqual(n('20:34 UTC'),'twenty thirty-four U T C');self.assertEqual(n('ratio 3:1'),'ratio 3:1')
        self.assertEqual(n('on 2026-10-26'),'on October 26, 2026');self.assertEqual(n('10/26/2026'),'October 26, 2026')
    def test_abbreviations(self):
        self.assertEqual(n('60 mph and 5 km'),'60 miles per hour and 5 kilometers');self.assertEqual(n('Lakers vs. Celtics, e.g. tonight'),'Lakers versus Celtics, for example tonight')
        self.assertEqual(n('10 kph'),'10 kilometers per hour');self.assertEqual(n('Tom & Jerry'),'Tom and Jerry')
    def test_markdown_emoji_and_urls_are_stripped(self):
        self.assertEqual(n('**Bold** `code` [link](http://x.com) 😀 ☀️'),'Bold code link')
        self.assertEqual(n('- one\n- two'),'one\ntwo');self.assertEqual(n('## Title\nSee https://example.com/a?b=1 now'),'Title\nSee now')
    def test_plain_speech_is_untouched_and_the_step_is_idempotent(self):
        plain='Overfitting is when a model memorizes its training data.';self.assertEqual(n(plain),plain)
        for text in ["It's 77°F, 3:45 PM on 10/26/2026, 51–71%, $2,309.82, 0.73%.",'**x** 60 mph']:self.assertEqual(n(n(text)),n(text))
    def test_runtime_cleaner_applies_it_before_the_existing_fixes(self):
        src=ast.parse(Path('julius_candidate.py').read_text());ns={'re':re,'normalize_for_speech':n}
        for node in src.body:
            if (isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='FIXES' for t in node.targets)) or (isinstance(node,ast.FunctionDef) and node.name=='clean_for_speech'):
                exec(compile(ast.Module(body=[node],type_ignores=[]),'<c>','exec'),ns)
        clean=ns['clean_for_speech']
        self.assertEqual(clean("It's 77°F and clear."),"It's 77 degrees and clear.");self.assertIn('A.I.',clean('The AI said 17% 🎉'));self.assertIn('17 percent',clean('The AI said 17% 🎉'))
class TranscriptFixTests(unittest.TestCase):
    def test_known_names_and_acronyms_are_repaired(self):
        from julius_speech_text import fix_transcript as fix
        self.assertEqual(fix("What's the best prize pick slip for tonight?"),"What's the best AcmeCorp slip for tonight?")
        self.assertEqual(fix('I checked the price picks line and the Prize Picks app'),'I checked the AcmeCorp line and the AcmeCorp app')
        self.assertEqual(fix('Who won the N.H.L. game and the N. F. L. game?'),'Who won the NHL game and the NFL game?')
        self.assertEqual(fix("What's their eath at right now?"),"What's their ETH at right now?");self.assertEqual(fix('the E.T.H. price'),'the ETH price')
    def test_ordinary_text_is_untouched(self):
        from julius_speech_text import fix_transcript as fix
        for text in ['Explain overfitting simply.','What is the price of Bitcoin?','Pick a prize for the winner.',"What's 17% of 2,480?",'Both teeth hurt, and Beth left','Seth and Heath came']:self.assertEqual(fix(text),text)
    def test_runtime_applies_the_fix_to_every_transcription(self):
        self.assertIn('text = fix_transcript(text)',Path('julius_candidate.py').read_text())
class SpeakableTests(unittest.TestCase):
    def test_weather(self):
        self.assertEqual(speakable('get_weather','Springfield: 76°F, clear, feels like 76. High 77, low 50.'),"It's 76 degrees and clear in Springfield, with a high of 77 and a low of 50.")
        self.assertIn('feels like 75',speakable('get_weather','Springfield: 74°F, clear, feels like 75. High 77, low 50.'))
    def test_forecast_tomorrow_and_multi_day(self):
        self.assertEqual(speakable('get_forecast','Springfield forecast:\nMon: 77/50, clear, 0% rain\nTue: 71/51, overcast, 10% rain',{'days':2}),"Tomorrow's Tuesday in Springfield: high of 71, low of 51, overcast, 10 percent chance of rain.")
        text=speakable('get_forecast','Tokyo forecast:\nSat: 60/50, rain, 83% rain\nSun: 78/55, rain, 90% rain\nMon: 65/52, showers, 100% rain',{'days':3})
        self.assertIn('Saturday: high of 60',text);self.assertIn('Monday: high of 65',text)
    def test_time_calculator_and_price(self):
        self.assertEqual(speakable('get_datetime','Paris: Monday, October 26, 9:34 PM (Europe/Paris) — 8 hours ahead of your time'),"It's Monday, October 26, 9:34 PM in Paris, 8 hours ahead of your time.")
        self.assertEqual(speakable('calculate','17% of 2480 = 421.6'),"That's 421.6.")
        quote=speakable('get_quote','Ethereum USD (ETH-USD): 2,309.82 USD, down 17.05 (0.73%) versus previous close. 52-week range 1,400.00 to 4,100.00 USD. As of 2026-10-26 20:34 UTC. Source: Yahoo Finance; quotes may be delayed.')
        self.assertEqual(quote,'Ethereum is about 2,310 dollars, down 0.73 percent from the previous close. Quotes may be delayed.')
    def test_prices_are_rounded_for_speech_unless_the_exact_price_is_asked_for(self):
        eth='Ethereum USD (ETH-USD): 2,304.98 USD, down 21.00 (0.94%) versus previous close.'
        self.assertIn('about 2,305 dollars',speakable('get_quote',eth));self.assertNotIn('cents',speakable('get_quote',eth))
        self.assertIn('2,304 dollars and 98 cents',speakable('get_quote',eth,exact=True))
        self.assertIn('42 dollars and 30 cents',speakable('get_quote','Acme (ACME): 42.30 USD, up 1.00 (2.42%) versus previous close.'))
        from julius_speech_text import EXACT_REQUEST
        for text in ['what is the exact price of ETH','price of ETH to the cent','precisely what is ETH at']:self.assertTrue(EXACT_REQUEST.search(text),text)
        self.assertFalse(EXACT_REQUEST.search("what's ETH at right now"))
    def test_router_rounds_a_price_and_gives_the_exact_one_when_asked(self):
        from julius_intents import LiveIntentRouter
        r=LiveIntentRouter();r.resolve("what's ETH at right now");q='Ethereum USD (ETH-USD): 2,304.98 USD, down 21.00 (0.94%) versus previous close.'
        self.assertIn('about 2,305',r.spoken_result('get_quote',q,{'symbol':'ETH'}))
        r.resolve('what is the exact price of ETH');self.assertIn('98 cents',r.spoken_result('get_quote',q,{'symbol':'ETH'}))
    def test_spoken_form_has_no_symbols_after_normalizing(self):
        for fn,out,args in [('get_weather','Springfield: 76°F, clear, feels like 74. High 77, low 50.',None),('get_quote','Tesla (TSLA): 241.50 USD, up 3.10 (1.30%) versus previous close.',None),('get_datetime','Paris: Monday, October 26, 9:34 PM (Europe/Paris)',None)]:
            said=n(speakable(fn,out,args));self.assertIsNotNone(said);self.assertFalse(re.search(r'[°%$:]|\d\.\d',said),said)
    def test_other_tools_and_unparseable_output_fall_back_to_the_model(self):
        for fn,out in [('list_files','a\nb'),('read_file','text'),('get_weather',"Couldn't find Nowhere."),('get_quote','No data for XYZ.'),('calculate','not an equation')]:self.assertIsNone(speakable(fn,out),fn)
from test_julius_call_counts import as_parts
class OneRoundTripTests(unittest.TestCase):
    def no_call(self):return SimpleNamespace(message=SimpleNamespace(content='',tool_calls=None))
    def tool_call(self,name,**args):return SimpleNamespace(message=SimpleNamespace(content='',tool_calls=[SimpleNamespace(function=SimpleNamespace(name=name,arguments=args))]))
    def run_turn(self,text,tool_phase,cloud,out='x'):
        ns=load_candidate_turn();ns['speakable']=speakable;ns['EXACT_REQUEST']=EXACT_REQUEST;ns['cloud_brain']=object() if cloud else None;calls=[];spoken=[]
        def llm(**kw):
            calls.append(kw)
            if kw.get('stream') and 'tools' not in kw:return iter([SimpleNamespace(message=SimpleNamespace(content='Second call answer.'))])
            r=(tool_phase.pop(0) if len(tool_phase)>1 else tool_phase[0]) if isinstance(tool_phase,list) else tool_phase
            return as_parts(r) if kw.get('stream') else r
        ns['llm_chat']=llm
        with patch.object(jt,'call',return_value=out):
            jt.dispatcher().last_result={'ok':True}
            result=ns['ask_streaming'](text,'',speak_fn=spoken.append)
        return result,spoken,calls
    def test_a_simple_tool_is_one_model_call_and_a_ready_sentence(self):
        weather='Springfield: 76°F, clear, feels like 76. High 77, low 50.'
        result,spoken,calls=self.run_turn('Is it cold outside in Springfield?',self.tool_call('get_weather',location='Springfield'),cloud=True,out=weather)
        self.assertEqual(len(calls),1);self.assertTrue(calls[0].get('stream'));self.assertEqual(spoken,[result]);self.assertTrue(result.startswith("It's 76 degrees"))
    def test_a_model_price_lookup_is_rounded_unless_the_exact_price_is_asked_for(self):
        eth='Ethereum USD (ETH-USD): 2,304.98 USD, down 21.00 (0.94%) versus previous close.'
        result,spoken,calls=self.run_turn('what is ETH trading at right now?',self.tool_call('get_quote',symbol='ETH'),cloud=True,out=eth)
        self.assertIn('about 2,305 dollars',result)
        result,spoken,calls=self.run_turn('what is the exact price of ETH right now?',self.tool_call('get_quote',symbol='ETH'),cloud=True,out=eth)
        self.assertIn('2,304 dollars and 98 cents',result)
    def test_the_same_holds_for_a_local_brain(self):
        result,spoken,calls=self.run_turn('Is it cold outside in Springfield?',self.tool_call('get_weather',location='Springfield'),cloud=False,out='Springfield: 76°F, clear, feels like 76. High 77, low 50.')
        self.assertEqual(len(calls),1)
    def test_tools_whose_output_needs_interpreting_keep_the_two_call_path(self):
        result,spoken,calls=self.run_turn('List the files in my Downloads folder',[self.tool_call('list_files',directory='Downloads'),self.no_call()],cloud=True,out='a.txt\nb.txt')
        self.assertEqual(len(calls),2);self.assertEqual(result,'Second call answer.')
    def test_several_tool_calls_in_one_turn_still_get_a_model_summary(self):
        two=SimpleNamespace(message=SimpleNamespace(content='',tool_calls=[SimpleNamespace(function=SimpleNamespace(name='get_weather',arguments={'location':'A'})),SimpleNamespace(function=SimpleNamespace(name='get_weather',arguments={'location':'B'}))]))
        result,spoken,calls=self.run_turn('What is the weather in A and B?',[two,self.no_call()],cloud=True,out='Springfield: 76°F, clear, feels like 76. High 77, low 50.')
        self.assertEqual(len(calls),2)
    def test_no_tool_text_answer_is_spoken_sentence_by_sentence_with_one_call(self):
        ns=load_candidate_turn();ns['cloud_brain']=object();ns['_tool_carry_next']=True;spoken=[];calls=[]
        ns['llm_chat']=lambda **kw:calls.append(kw) or as_parts(SimpleNamespace(message=SimpleNamespace(content='Overfitting is when a model memorizes its training data. It then fails on new data.',tool_calls=None)))
        result=ns['ask_streaming']('explain overfitting simply','',speak_fn=spoken.append)
        self.assertEqual(len(calls),1);self.assertEqual(spoken,['Overfitting is when a model memorizes its training data.','It then fails on new data.'])
    def test_local_brain_keeps_its_streamed_second_call_for_text_answers(self):
        ns=load_candidate_turn();ns['cloud_brain']=None;ns['_tool_carry_next']=True;calls=[]
        def llm(**kw):
            calls.append(kw)
            return iter([SimpleNamespace(message=SimpleNamespace(content='Streamed answer.'))]) if kw.get('stream') else SimpleNamespace(message=SimpleNamespace(content='tool-phase text',tool_calls=None))
        ns['llm_chat']=llm;ns['ask_streaming']('explain overfitting simply','',speak_fn=lambda s:None)
        self.assertEqual([bool(c.get('stream')) for c in calls],[False,True])
class PolicyPromptTests(unittest.TestCase):
    def test_prompts_state_the_spoken_style_and_the_tool_policy(self):
        self.assertIn('spoken aloud',SPOKEN_STYLE_PROMPT);self.assertIn('77 degrees',SPOKEN_STYLE_PROMPT);self.assertIn('no symbols, lists, markdown',SPOKEN_STYLE_PROMPT)
        for phrase in ('stable knowledge','explanations, definitions, general facts','live data','exact math','no tool'):self.assertIn(phrase,TOOL_POLICY_PROMPT)
        from julius_speech_text import BREVITY_PROMPT
        for phrase in ('one to three sentences','core idea in two or three sentences','Want more detail?','only when the user asks','about 2,305 dollars','exact price'):self.assertIn(phrase,BREVITY_PROMPT)
    def test_cloud_system_prompt_includes_both_and_local_prompts_do_not(self):
        src=Path('julius_candidate.py').read_text();self.assertIn('SYSTEM += julius_cloud.PLANNING_PROMPT + SPOKEN_STYLE_PROMPT + TOOL_POLICY_PROMPT + BREVITY_PROMPT',src)
        from julius_conversation import conversation_messages
        with patch.dict(os.environ,{'JULIUS_BACKEND':'cloud'}):
            said=conversation_messages([{'role':'user','content':'hi'}])[0]['content'];self.assertIn('spoken aloud',said);self.assertIn('Want more detail?',said)
        with patch.dict(os.environ,{'JULIUS_BACKEND':'ollama'}):
            said=conversation_messages([{'role':'user','content':'hi'}])[0]['content'];self.assertNotIn('spoken aloud',said);self.assertNotIn('Want more detail?',said)
if __name__=='__main__':unittest.main()
