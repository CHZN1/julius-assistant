"""Cancel a real isolated MLX voice stream and verify the next response recovers."""
import json,os,time,threading
from pathlib import Path
from julius_benchmark_paths import BENCHMARK_DIR
os.environ['JULIUS_TTS']='qwen-mlx'
from julius_tts import SpeechEngine
from julius_cancellation import TurnCancelled
engine=SpeechEngine();engine.cancel_event=threading.Event();first_pid=engine.worker.pid
try:
    stream=engine.synthesize('Great, that worked! Now let us carefully examine every part of the result before deciding what to do next.')
    next(stream);engine.cancel_event.set();started=time.perf_counter()
    try:next(stream)
    except TurnCancelled:pass
    else:raise AssertionError('Cancelled stream emitted another chunk')
    elapsed=time.perf_counter()-started
    assert engine.worker is None or engine.worker.poll() is not None
    engine.cancel_event.clear()
    chunks=list(engine.synthesize('I am here, the user.'))
    assert chunks and engine.worker.pid!=first_pid
    result={'cancel_seconds':elapsed,'recovery_chunks':len(chunks),'worker_restarted':True}
    (BENCHMARK_DIR/'voice_cancel.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
finally:engine.close()
