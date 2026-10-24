"""Separate cold model load, prompt evaluation and generation without audio."""
import json,time
from test_julius_candidate import load_candidate_turn
from julius_backend import llm_chat,close_backend
from julius_benchmark_paths import BENCHMARK_DIR
rows=[]
try:
 for context in (4096,2048):
  ns=load_candidate_turn();last={}
  def measured_chat(**kwargs):
   kwargs['options']={**kwargs.get('options',{}),'num_ctx':context}
   result=llm_chat(**kwargs)
   if not kwargs.get('stream'):return result
   def parts():
    try:
     for part in result:
      for key in ('load_duration','prompt_eval_duration','eval_duration','prompt_eval_count','eval_count'):
       value=getattr(part,key,None)
       if value is not None:last[key]=value
      yield part
    finally:
     if hasattr(result,'close'):result.close()
   return parts()
  ns['llm_chat']=measured_chat
  for prompt in ('Explain overfitting simply.','How would I avoid that?','Explain it without technical terms.'):
   close_backend();last.clear();started=time.perf_counter()
   answer=ns['ask_streaming'](prompt,'',speak_fn=lambda _:None)
   row={'context':context,'prompt':prompt,'answer':answer,'seconds':time.perf_counter()-started,**last}
   rows.append(row);ns['history'] += [{'role':'user','content':prompt},{'role':'assistant','content':answer}]
   print(json.dumps(row),flush=True)
   (BENCHMARK_DIR/'reasoning_startup.json').write_text(json.dumps(rows,indent=2))
finally:close_backend()
