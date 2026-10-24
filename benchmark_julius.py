"""Run one backend per process, saving latency, correctness and memory evidence."""
import argparse, ast, json, resource, time
from pathlib import Path
from julius_backend import llm_chat, LLM_BACKEND, LLM
import julius_tools as jt
from julius_benchmark_checks import passed_case
p=argparse.ArgumentParser(); p.add_argument('--output', required=True); p.add_argument('--rounds',type=int,default=2); args=p.parse_args()
source=ast.parse(Path('julius.py').read_text())
system=next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SYSTEM' for t in n.targets))
cases=[('Explain overfitting in one sentence.',None,None),('What is 17 times 4839?','calculate',None),("What's the weather today?",'get_weather',None),('What time is it in Tokyo?','get_datetime','Tokyo'),('What time is it in Egypt?','get_datetime','Egypt'),("What's the weather in New York?",'get_weather','New York'),("What's the forecast for this week?",'get_forecast',None),("What's Bitcoin trading at?",'get_quote',None),('What time is it?','get_datetime',None)]
result={'backend':LLM_BACKEND,'model':LLM,'cases':[]}
try:
 for run in range(args.rounds):
  for text,tool,location in cases:
   msgs=[{'role':'system','content':system},{'role':'user','content':text}]
   t=time.perf_counter()
   r=llm_chat(messages=msgs,tools=jt.SCHEMAS if tool else None,options={'temperature':0,'num_predict':256,'num_ctx':4096})
   elapsed=time.perf_counter()-t
   calls=[{'name':c.function.name,'arguments':dict(c.function.arguments)} for c in (r.message.tool_calls or [])]
   passed=passed_case(text,calls,r.message.content or '')
   row={'round':run,'prompt':text,'seconds':round(elapsed,3),'calls':calls,'content':r.message.content,'passed':passed,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
   if LLM_BACKEND=='ollama':
    import ollama
    row['generation_metadata']={k:getattr(r,k,None) for k in ['eval_count','eval_duration','prompt_eval_count','prompt_eval_duration','load_duration']}
    row['reasoning_present']=bool(getattr(r.message,'thinking',None))
    row['resident_models']=[{'name':m.model,'size_bytes':m.size,'vram_bytes':m.size_vram} for m in ollama.ps().models]
   else:
    import mlx.core as mx
    row['mlx_active_bytes']=mx.get_active_memory(); row['mlx_cache_bytes']=mx.get_cache_memory()
   result['cases'].append(row); print(json.dumps(row),flush=True)
   Path(args.output).write_text(json.dumps(result,indent=2))
finally:
 if LLM_BACKEND=='ollama':
  import ollama
  ollama.generate(model=LLM,prompt='',keep_alive=0)
 else:
  import mlx.core as mx
  result['mlx_peak_bytes']=mx.get_peak_memory()
 Path(args.output).write_text(json.dumps(result,indent=2))
