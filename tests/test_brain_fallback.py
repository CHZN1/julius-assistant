"""The default Qwen brain falls back to Llama only when it is missing or cannot load."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import ollama
import julius.brain.backend as backend

def chunk(text):return SimpleNamespace(message=SimpleNamespace(content=text,tool_calls=None))
class FallbackTests(unittest.TestCase):
 def setUp(self):
  self.patches=[patch.object(backend,'LLM_BACKEND','ollama'),patch.object(backend,'LLM','primary'),
   patch.object(backend,'OLLAMA_FALLBACK','backup'),patch.object(backend,'_ACTIVE_OLLAMA',None),patch.object(backend,'_BACKEND_USED',False)]
  for p in self.patches:p.start()
  self.addCleanup(lambda:[p.stop() for p in reversed(self.patches)])
 def chat(self,**kw):return backend.llm_chat(messages=[{'role':'user','content':'hi'}],**kw)
 def test_explicit_ollama_backend_defaults_to_qwen3_4b_instruct_with_llama_fallback(self):
  import importlib,os
  with patch.dict(os.environ,{},clear=False):
   for k in ('JULIUS_MODEL','JULIUS_FALLBACK_MODEL'):os.environ.pop(k,None)
   os.environ['JULIUS_BACKEND']='ollama'  # the unset default is the cloud profile (test_julius_default_profile)
   fresh=importlib.reload(backend)
   try:
    self.assertEqual(fresh.LLM,'qwen3:4b-instruct-2507-q4_K_M');self.assertEqual(fresh.OLLAMA_FALLBACK,'llama3.2:3b')
   finally:importlib.reload(backend)
 def test_primary_is_used_when_available(self):
  calls=[]
  with patch('ollama.chat',side_effect=lambda model,**kw:calls.append(model) or SimpleNamespace(message='ok')):
   self.chat()
  self.assertEqual(calls,['primary'])
 def test_missing_primary_falls_back_and_stays_until_release(self):
  calls=[]
  def fake(model,**kw):
   calls.append(model)
   if model=='primary':raise ollama.ResponseError('model not found',404)
   return SimpleNamespace(message='ok')
  with patch('ollama.chat',side_effect=fake),patch('ollama.generate') as generate:
   self.chat();self.chat()
   self.assertEqual(calls,['primary','backup','backup'])
   backend._BACKEND_USED=True;backend.close_backend()
   generate.assert_called_once_with(model='backup',prompt='',keep_alive=0)
   self.chat()
  self.assertEqual(calls[-2:],['primary','backup'])  # primary is retried first after the release
 def test_streaming_falls_back_before_the_first_token(self):
  calls=[]
  def fake(model,**kw):
   calls.append(model)
   if model=='primary':raise ollama.ResponseError('model not found',404)
   return iter([chunk('a'),chunk('b')])
  with patch('ollama.chat',side_effect=fake):
   text=''.join(p.message.content for p in self.chat(stream=True))
  self.assertEqual((text,calls),('ab',['primary','backup']))
 def test_out_of_memory_error_falls_back(self):
  def fake(model,**kw):
   if model=='primary':raise ollama.ResponseError('model requires more system memory',500)
   return SimpleNamespace(message='ok')
  with patch('ollama.chat',side_effect=fake):self.chat()
  self.assertEqual(backend._ACTIVE_OLLAMA,'backup')
 def test_unrelated_errors_are_not_masked(self):
  with patch('ollama.chat',side_effect=ollama.ResponseError('bad request',400)):
   with self.assertRaises(ollama.ResponseError):self.chat()
  with patch.object(backend,'OLLAMA_FALLBACK',''),patch('ollama.chat',side_effect=ollama.ResponseError('model not found',404)):
   with self.assertRaises(ollama.ResponseError):self.chat()
if __name__=='__main__':unittest.main()
