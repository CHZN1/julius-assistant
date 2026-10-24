"""Same celebratory phrase with both selected speaker references; no identity change."""
import json,time,wave
from pathlib import Path
import mlx.core as mx
import numpy as np
import sounddevice as sd
from julius_mlx_voice_support import load_voice_model,load_reference_prompts
from julius_voice_identity import REF_TEXT
mx.set_cache_limit(256*1024**2);mx.set_memory_limit(5*1024**3)
model=load_voice_model();refs=load_reference_prompts(model)
model.speech_tokenizer.encoder_model=None;mx.clear_cache()
out=Path('benchmarks/2026-10-02/supervised_delivery');out.mkdir(parents=True,exist_ok=True)
rows=[];text='Great, that worked!'
for mode in ['balanced','lift']:
    print('Generating '+mode+' reference: '+text,flush=True)
    mx.random.seed(1301);config=model.generate_config;parts=[]
    gen=model._generate_icl(text=text,ref_audio=refs[mode],ref_text=REF_TEXT,language='English',
        temperature=config['temperature'],top_k=config['top_k'],top_p=config['top_p'],
        repetition_penalty=config['repetition_penalty'],max_tokens=96,stream=True,streaming_interval=.32)
    for result in gen:
        audio=np.asarray(result.audio,dtype=np.float32)
        if not np.isfinite(audio).all():raise FloatingPointError('Nonfinite audio')
        parts.append(audio)
    path=out/f'{mode}.wav';audio=np.concatenate(parts)
    with wave.open(str(path),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000)
        wav.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())
    print('Playing '+mode+' reference: '+text,flush=True)
    sd.play(audio,24000);sd.wait()
    rows.append({'reference':mode,'text':text,'path':str(path),'playback':'complete cached audio'})
    (out/'results.json').write_text(json.dumps(rows,indent=2));time.sleep(1)
