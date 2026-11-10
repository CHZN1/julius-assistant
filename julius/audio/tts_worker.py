"""Qwen speech worker with isolated Transformers dependencies and JSON-line IPC."""
import contextlib, json, os, sys, tempfile
from pathlib import Path
os.environ['JULIUS_TTS_WORKER']='1'
os.environ['JULIUS_TTS']='qwen-clone'
import numpy as np
from julius.audio.tts import SpeechEngine
with contextlib.redirect_stdout(sys.stderr):
    engine=SpeechEngine()
print(json.dumps({'ready':True}),flush=True)
with tempfile.TemporaryDirectory(prefix='julius_tts_',dir=__import__('julius.state').tmp_dir()) as directory:
    for line in sys.stdin:
        try:
            req=json.loads(line)
            if req.get('op')=='close': break
            mode=req.get('delivery_mode')
            if mode not in (None,'balanced','lift'):raise ValueError('Unknown delivery mode')
            engine.response_delivery_mode=mode
            with contextlib.redirect_stdout(sys.stderr):
                audio,sr=next(engine.synthesize(req['text']))
            path=Path(directory)/'speech.npy';np.save(path,audio)
            import torch
            metrics={'path':str(path),'sample_rate':sr,'delivery_mode':engine.delivery_mode,'voice_strategy':engine.strategy,'dtype':engine.dtype_name,'generation_chunks':engine.generation_chunks,'allocation_retries':engine.allocation_retries,'mps_allocated_bytes':torch.mps.current_allocated_memory(),'mps_driver_bytes':torch.mps.driver_allocated_memory()}
            torch.mps.empty_cache()
            metrics['mps_driver_after_cache_release_bytes']=torch.mps.driver_allocated_memory()
            print(json.dumps(metrics),flush=True)
        except Exception as exc:
            import traceback
            traceback.print_exc(file=sys.stderr)
            import torch
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
            print(json.dumps({'error':f'{type(exc).__name__}: {exc}'}),flush=True)
