"""Compare fully restarted and empty retained workers without playing audio."""
import json,os,time
from julius_tts import SpeechEngine
from julius_benchmark_paths import BENCHMARK_DIR
os.environ['JULIUS_TTS']='qwen-mlx'
rows=[]
for keep in ('0','1'):
    os.environ['JULIUS_MLX_KEEP_WORKER']=keep
    engine=SpeechEngine()
    try:
        for cycle in range(3):
            pid=engine.worker.pid
            t=time.perf_counter();engine.suspend();unload=time.perf_counter()-t
            retained=engine.worker is not None
            t=time.perf_counter();stream=engine.synthesize('Great, that worked!')
            next(stream);first=time.perf_counter()-t
            for chunk in stream:pass
            rows.append({'keep_worker':keep,'cycle':cycle,'unload_seconds':unload,
                'first_audio_seconds':first,'retained':retained,
                'same_pid':engine.worker.pid==pid,'unload_metrics':getattr(engine,'unload_metrics',None)})
            print(json.dumps(rows[-1]),flush=True)
    finally:engine.close()
(BENCHMARK_DIR/'voice_handoff.json').write_text(json.dumps(rows,indent=2))
