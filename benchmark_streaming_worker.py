"""Exercise real worker restart, style changes, long spans, and consumer cancellation."""
import json, os, time
from pathlib import Path
import numpy as np
import soundfile as sf
from julius_tts import SpeechEngine
os.environ['JULIUS_TTS']='qwen-mlx'
out=Path('benchmarks/2026-10-02/mlx_worker');out.mkdir(parents=True,exist_ok=True)
started=time.perf_counter();engine=SpeechEngine();load=time.perf_counter()-started
rows=[]
try:
    for text in ['Hey the user, I am here.','Great, that worked! I am excited to see what comes next.',
        'We should check these results carefully before changing anything.',
        'Overfitting happens when a model gets too closely tied to the training data, making it perform really well on that data but poorly on new examples.',
        'Reminder: the user, this is your Julius reminder test.']:
        started=time.perf_counter();pieces=[];arrivals=[]
        for audio,rate in engine.synthesize(text):
            arrivals.append({'seconds':time.perf_counter()-started,'audio_seconds':len(audio)/rate})
            pieces.append(audio)
        path=out/f'{len(rows)}.wav';sf.write(path,np.concatenate(pieces),rate)
        rows.append({'text':text,'first_audio_seconds':arrivals[0]['seconds'],
            'seconds':time.perf_counter()-started,'arrivals':arrivals,'path':str(path),
            'metrics':engine.worker_metrics})
        (out/'results.json').write_text(json.dumps({'load_seconds':load,'cases':rows},indent=2))
        print(json.dumps(rows[-1]),flush=True)
    gen=engine.synthesize('This stream will be interrupted.');next(gen);gen.close()
    assert engine.worker is None
    started=time.perf_counter();list(engine.synthesize('I am back.'))
    result={'load_seconds':load,'cases':rows,'cancel_restart_passed':True,
            'restart_speech_seconds':time.perf_counter()-started}
    (out/'results.json').write_text(json.dumps(result,indent=2))
finally:engine.close()
