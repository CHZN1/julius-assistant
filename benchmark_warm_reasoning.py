"""Guarded residency trial; ASR and voice remain sequential, no real playback."""
import ast,json,os,time
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly
os.environ['JULIUS_TTS']='qwen-mlx';os.environ['JULIUS_MLX_KEEP_WORKER']='1'
os.environ['JULIUS_MLX_ASR_KEEP_VOICE']='0'
from julius_tts import SpeechEngine
from julius_backend import close_backend
from julius_asr_resources import recognition_resources
from julius_model_resources import mac_memory_snapshot
from julius_benchmark_paths import BENCHMARK_DIR
from test_julius_candidate import load_candidate_turn
import mlx_whisper
engine=SpeechEngine();rows=[]
texts=['Explain overfitting simply.','How would I avoid that?','Explain it without technical terms.']
try:
 inputs=[]
 for text in texts:
  chunks=list(engine.synthesize(text));inputs.append(resample_poly(np.concatenate([a for a,_ in chunks]),2,3))
 fn=next(n for n in ast.parse(Path('julius_candidate.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='transcribe')
 scope={'os':os,'tts':engine,'close_backend':close_backend,'recognition_resources':recognition_resources,'mlx_whisper':mlx_whisper,'ASR':'mlx-community/whisper-large-v3-turbo'}
 exec(compile(ast.Module(body=[fn],type_ignores=[]),'transcribe','exec'),scope)
 for mode in ('sequential','warm'):
  close_backend();engine.suspend();ns=load_candidate_turn()
  for index,audio in enumerate(inputs):
   engine.suspend()
   if mode=='sequential':close_backend()
   state=mac_memory_snapshot()
   if mode=='warm' and state['free_percent']<35:raise RuntimeError('Not enough headroom for residency trial')
   os.environ['JULIUS_ASR_RELEASE_LLM']='1' if mode=='sequential' else '0'
   start=time.perf_counter();text,lang,nsp=scope['transcribe'](audio);asr=time.perf_counter()-start
   if not text or nsp>.6:raise RuntimeError('Trial input was not recognized')
   t=time.perf_counter();answer=ns['ask_streaming'](text,'',speak_fn=lambda _:None,before_inference=engine.suspend);reason=time.perf_counter()-t
   ns['history'] += [{'role':'user','content':text},{'role':'assistant','content':answer}]
   if mode=='sequential':close_backend()
   state=mac_memory_snapshot()
   if mode=='warm' and state['free_percent']<45:raise RuntimeError('Not enough headroom to load voice beside brain')
   t=time.perf_counter();stream=engine.synthesize(answer);next(stream);first=time.perf_counter()-t
   for chunk in stream:pass
   row={'mode':mode,'index':index,'recognized':text,'answer':answer,'asr_seconds':asr,'reasoning_seconds':reason,'speech_first_audio_seconds':first,'input_to_first_audio_seconds':asr+reason+first,'after_speech':mac_memory_snapshot()}
   rows.append(row);(BENCHMARK_DIR/'warm_reasoning.json').write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
finally:engine.close();close_backend()
