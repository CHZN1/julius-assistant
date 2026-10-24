"""Controlled synthesized audio check; not a substitute for the user's mic."""
import os,time,json
import numpy as np
from scipy.signal import resample_poly
from julius_tts import SpeechEngine
from julius_asr_resources import recognition_resources
from julius_benchmark_paths import BENCHMARK_DIR
os.environ['JULIUS_TTS']='qwen-mlx'
engine=SpeechEngine()
try:
 chunks=list(engine.synthesize('Julius, explain overfitting simply.'))
 audio=resample_poly(np.concatenate([a for a,_ in chunks]),2,3)
finally:engine.close()
import mlx_whisper
rows=[]
for options in ({},{'language':'en','initial_prompt':'Julius, the user, Qwen, MLX, Ollama. Overfitting, regularization, training data.'}):
 for trial in range(2):
  t=time.perf_counter()
  with recognition_resources():
   result=mlx_whisper.transcribe(audio,path_or_hf_repo='mlx-community/whisper-large-v3-turbo',condition_on_previous_text=False,no_speech_threshold=.6,**options)
  rows.append({'options':options,'trial':trial,'seconds':time.perf_counter()-t,'text':result['text']})
  print(json.dumps(rows[-1]),flush=True)
(BENCHMARK_DIR/'recognition_options.json').write_text(json.dumps(rows,indent=2))
