"""Paired Torch baseline and cross-runtime speaker-embedding comparison."""
import os
os.environ['JULIUS_TTS']='qwen-clone';os.environ['JULIUS_TTS_WORKER']='1'
import json,time
from pathlib import Path
import numpy as np
import soundfile as sf
import torch
from julius_tts import SpeechEngine
from julius_voice_identity import delivery_mode
out=Path('benchmarks/2026-10-02/voice_paired');out.mkdir(parents=True,exist_ok=True)
started=time.perf_counter();engine=SpeechEngine();load_seconds=time.perf_counter()-started
rows=[];similarities=[]
def similarity(audio,rate):
    with torch.inference_mode():
        emb=engine.model.model.extract_speaker_embedding(audio=audio,sr=rate).float().flatten()
    return {mode:float(torch.nn.functional.cosine_similarity(emb,prompt[0].ref_spk_embedding.float().flatten(),dim=0))
            for mode,prompt in engine.prompts.items()}
def save():
    (out/'results.json').write_text(json.dumps({'load_seconds':load_seconds,'cases':rows,'mlx_speaker_similarity':similarities},indent=2))
texts=['Hey the user, I am here.','Great, that worked! I am excited to see what comes next.',
       'Reminder: the user, this is your Julius reminder test.']
for repeat in range(2):
    for index,text in enumerate(texts):
        torch.manual_seed(1301+index);engine.delivery_mode=delivery_mode(text)
        started=time.perf_counter();cpu=time.process_time()
        audio,rate=engine._generate_qwen(text);torch.mps.synchronize()
        elapsed=time.perf_counter()-started
        path=out/f'{repeat}_{index}.wav';sf.write(path,audio,rate)
        row={'repetition':repeat,'text':text,'mode':engine.delivery_mode,'seconds':elapsed,
             'cpu_seconds':time.process_time()-cpu,'audio_seconds':len(audio)/rate,
             'speaker_cosine':similarity(audio,rate),'path':str(path)}
        rows.append(row);save();print(json.dumps(row),flush=True);torch.mps.empty_cache()
for row in json.loads(Path('benchmarks/2026-10-02/mlx_decoder_fixed/results.json').read_text())['cases']:
    audio,rate=sf.read(row['path'],dtype='float32')
    similarities.append({'path':row['path'],'mode':row['mode'],'stream':row['stream'],
        'interval':row['interval'],'speaker_cosine':similarity(audio,rate)})
    torch.mps.empty_cache();save()
engine.close()
