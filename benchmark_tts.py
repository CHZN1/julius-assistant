from julius_benchmark_paths import BENCHMARK_DIR
import argparse, json, os, resource, time
from pathlib import Path
import soundfile as sf
from julius_tts import SpeechEngine
parser=argparse.ArgumentParser();parser.add_argument('--long',action='store_true');args=parser.parse_args()
name=os.environ.get('JULIUS_TTS','kokoro')
result={'backend':name,'cases':[]}
t=time.perf_counter(); engine=SpeechEngine(); result['load_seconds']=time.perf_counter()-t
texts=['Hey the user. I found something interesting.','Tokyo is sixteen hours ahead of you.','Hey the user. I found something interesting.']
if args.long:
 texts.append('Overfitting happens when a model learns the training data too closely and performs poorly on new examples. More varied data, regularization, and early stopping can help.')
for i,text in enumerate(texts):
 t=time.perf_counter(); chunks=list(engine.synthesize(text)); elapsed=time.perf_counter()-t
 audio,sr=chunks[0]; sf.write(str(BENCHMARK_DIR/f'benchmark_{name}_{i}.wav'),audio,sr)
 duration=sum(len(a)/s for a,s in chunks)
 row={'text':text,'seconds':elapsed,'audio_seconds':duration,'real_time_factor':elapsed/duration,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
 if name!='kokoro':
  import torch
  if hasattr(engine,'worker_metrics'):
   row.update({k:v for k,v in engine.worker_metrics.items() if k.endswith('_bytes')})
  else:
   row['mps_allocated_bytes']=torch.mps.current_allocated_memory(); row['mps_driver_bytes']=torch.mps.driver_allocated_memory()
 result['cases'].append(row); print(json.dumps(row),flush=True)
 (BENCHMARK_DIR/f'benchmark_tts_{name}.json').write_text(json.dumps(result,indent=2))

engine.close()
