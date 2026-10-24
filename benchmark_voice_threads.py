"""Compare CPU thread counts without changing speaker, prompts, dtype or sampling."""
import os
os.environ['JULIUS_TTS']='qwen-clone';os.environ['JULIUS_TTS_WORKER']='1'
import json,time
from pathlib import Path
import numpy as np
import soundfile as sf
import torch
from julius_tts import SpeechEngine
root=Path('benchmarks/2026-10-02/voice_threads');root.mkdir(parents=True,exist_ok=True)
engine=SpeechEngine();original=torch.get_num_threads();rows=[]
try:
 for count in [1,4,original]:
  torch.set_num_threads(count)
  for i,text in enumerate(['Reminder: the user, this is your Julius reminder test.','Hey the user, I am here.','Hey the user, I am here.']):
   torch.manual_seed(1301+i);engine.delivery_mode='balanced'
   started=time.perf_counter();cpu=time.process_time();audio,rate=engine._generate_qwen(text);torch.mps.synchronize()
   assert len(audio) and np.isfinite(audio).all()
   row={'threads':count,'case':i,'text':text,'seconds':time.perf_counter()-started,'cpu_seconds':time.process_time()-cpu,'audio_seconds':len(audio)/rate,'mps_driver_bytes':torch.mps.driver_allocated_memory()}
   rows.append(row);sf.write(str(root/f'threads_{count}_{i}.wav'),audio,rate)
   (root/'results.json').write_text(json.dumps({'original_threads':original,'cases':rows},indent=2));print(json.dumps(row),flush=True)
finally:engine.close()
