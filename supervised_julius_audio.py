"""Supervised speaker acceptance using real continuous streaming playback."""
import os,json,time
from pathlib import Path
import numpy as np
import sounddevice as sd
import soundfile as sf
os.environ['JULIUS_TTS']='qwen-mlx'
from julius_tts import SpeechEngine
out=Path('benchmarks/2026-10-02/supervised_audio');out.mkdir(parents=True,exist_ok=True)
engine=SpeechEngine();rows=[]
try:
    for label,text in [('balanced','Hey the user, I am here.'),
        ('lift','Great, that worked! I am excited to see what comes next.'),
        ('serious','We should check these results carefully before changing anything.')]:
        print('Playing '+label+': '+text,flush=True)
        start=time.perf_counter();parts=[];first=None;underflows=0
        gen=engine.synthesize(text)
        try:
            audio,rate=next(gen);first=time.perf_counter()-start;parts.append(audio)
            with sd.OutputStream(samplerate=rate,channels=1,dtype='float32') as output:
                underflows+=bool(output.write(audio.reshape(-1,1)))
                for audio,sr in gen:
                    if sr!=rate:raise RuntimeError('Sample rate changed')
                    parts.append(audio);underflows+=bool(output.write(audio.reshape(-1,1)))
        finally:gen.close()
        path=out/f'{label}.wav';sf.write(path,np.concatenate(parts),rate)
        rows.append({'label':label,'text':text,'first_audio_seconds':first,'seconds':time.perf_counter()-start,
            'output_underflows':underflows,'path':str(path),'metrics':engine.worker_metrics})
        (out/'results.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows[-1]),flush=True)
        time.sleep(.7)
finally:engine.close()
