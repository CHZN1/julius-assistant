from julius_benchmark_paths import BENCHMARK_DIR
import json, time
from pathlib import Path
from test_julius_candidate import load_candidate_turn
from julius_backend import LLM_BACKEND, LLM
ns=load_candidate_turn(); rows=[]
for text in ['Explain overfitting in one sentence.', 'How could I prevent that?', 'I am building Julius to run locally on my Mac. What should I prioritize?', 'What did I just say I was building?', 'Tell me a short joke about programmers.', 'Why do people like learning new things?']:
 spoken=[]; t=time.perf_counter(); answer=ns['ask_streaming'](text,'',speak_fn=spoken.append); seconds=time.perf_counter()-t
 ns['history'] += [{'role':'user','content':text},{'role':'assistant','content':answer}]
 ns['history'][:] = [ns['history'][0]]+ns['history'][-8:]
 rows.append({'prompt':text,'answer':answer,'seconds':seconds,'spoken_chunks':spoken})
 (BENCHMARK_DIR/f'benchmark_conversation_{LLM_BACKEND}_{LLM.replace(":","_").replace("/","_")}.json').write_text(json.dumps(rows,indent=2))
if LLM_BACKEND=='ollama':
 import ollama; ollama.generate(model=LLM,prompt='',keep_alive=0)
