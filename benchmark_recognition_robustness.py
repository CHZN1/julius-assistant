"""Recorded synthetic fixtures; compare recognition, not human-microphone accuracy."""
import json,time
from pathlib import Path
import numpy as np,soundfile as sf,mlx_whisper
from scipy.signal import resample_poly
from julius_asr_resources import recognition_resources
from julius_voice_identity import BALANCED_REFERENCE,LIFT_REFERENCE
out=Path('benchmarks/2026-10-05/recognition_robustness.json')
rows=[]
fixtures=[BALANCED_REFERENCE,LIFT_REFERENCE,Path('benchmarks/2026-10-05/span_audio/speech_span_36_0.wav')]
for path in fixtures:
 audio,sr=sf.read(path,dtype='float32');audio=resample_poly(audio,16000,sr)
 for condition in ('clean','quiet_noise'):
  samples=audio.copy()
  if condition=='quiet_noise':
   samples=samples*.25+np.random.default_rng(1301).normal(0,.001,len(samples)).astype(np.float32)
  samples=np.pad(samples,(8000,14400))
  started=time.perf_counter()
  with recognition_resources():
   result=mlx_whisper.transcribe(samples,path_or_hf_repo='mlx-community/whisper-large-v3-turbo',condition_on_previous_text=False,language='en',no_speech_threshold=.6)
  row={'file':str(path),'condition':condition,'seconds':time.perf_counter()-started,'text':result['text'],'segments':[{k:s.get(k) for k in ('avg_logprob','no_speech_prob','temperature')} for s in result.get('segments',[])]}
  rows.append(row);out.write_text(json.dumps(rows,indent=2));print(json.dumps(row),flush=True)
