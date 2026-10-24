"""Check reminder openings without modifying voice identity or runtime wording."""
import argparse,os,json,time
from pathlib import Path
import numpy as np
import soundfile as sf
os.environ['JULIUS_TTS']='qwen-mlx'
from julius_tts import SpeechEngine
p=argparse.ArgumentParser();p.add_argument('--output',default='benchmarks/2026-10-02/mlx_wording');p.add_argument('--repeat',type=int,default=1);p.add_argument('--natural',action='store_true');args=p.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
e=SpeechEngine();rows=[]
try:
    texts=['Reminder. the user, this is your Julius reminder test.',
                 'Reminder, the user, this is your Julius reminder test.',
                 'the user, this is a reminder from Julius.',
                 'Reminder: your meeting starts in ten minutes.',
                 'Reminder. Your meeting starts in ten minutes.']
    if args.natural:texts=['Here is your reminder. the user, this is your Julius reminder test.',
                          'Here is your reminder. This reminder recovered after Julius restarted.',
                          'Here is your reminder. Your meeting starts in ten minutes.']
    for text in texts * args.repeat:
        parts=[];start=time.perf_counter()
        for audio,rate in e.synthesize(text):parts.append(audio)
        path=out/f'{len(rows)}.wav';sf.write(path,np.concatenate(parts),rate)
        rows.append({'text':text,'path':str(path),'seconds':time.perf_counter()-start})
        (out/'results.json').write_text(json.dumps(rows,indent=2))
finally:e.close()
