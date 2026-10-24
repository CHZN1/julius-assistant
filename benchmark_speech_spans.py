"""Measure generation gaps for identical text; no playback or microphone."""
import os,json,time
from julius_tts import SpeechEngine
from julius_benchmark_paths import BENCHMARK_DIR
os.environ['JULIUS_TTS']='qwen-mlx'
engine=SpeechEngine();rows=[]
text="Overfitting happens when a model is too complex and fits the training data too well, but doesn't generalize well to new, unseen data."
try:
 for limit in (12,36):
  os.environ['JULIUS_MLX_SPAN_WORDS']=str(limit)
  for trial in range(2):
   t=time.perf_counter();chunks=[];wave=[]
   for audio,sr in engine.synthesize(text):
    chunks.append({'arrival':time.perf_counter()-t,'duration':len(audio)/sr});wave.append(audio)
   import numpy as np,soundfile as sf
   sf.write(BENCHMARK_DIR/f'speech_span_{limit}_{trial}.wav',np.concatenate(wave),sr)
   start=chunks[0]['arrival'];end=start;gaps=[]
   for chunk in chunks:
    gaps.append(max(0,chunk['arrival']-end));end=max(end,chunk['arrival'])+chunk['duration']
   row={'words_per_span':limit,'trial':trial,'first_audio_seconds':start,'generation_seconds':time.perf_counter()-t,'audio_seconds':sum(c['duration'] for c in chunks),'simulated_gap_seconds':sum(gaps),'maximum_gap_seconds':max(gaps),'chunks':chunks,'worker':engine.worker_metrics}
   rows.append(row);(BENCHMARK_DIR/'speech_spans.json').write_text(json.dumps(rows,indent=2));print(json.dumps({k:v for k,v in row.items() if k not in ('chunks','worker')}),flush=True)
finally:engine.close()
