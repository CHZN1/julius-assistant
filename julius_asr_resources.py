"""Scoped MLX transcription allocation guidance and reliable model release."""
from contextlib import contextmanager
@contextmanager
def recognition_resources(release_model=True):
    import gc,importlib
    import mlx.core as mx
    previous_cache=mx.set_cache_limit(128*1024**2)
    previous_memory=mx.set_memory_limit(3*1024**3)
    try:
        yield
    finally:
        try:
            mx.synchronize()
            if release_model:
                holder=importlib.import_module('mlx_whisper.transcribe').ModelHolder
                holder.model=None;holder.model_path=None
                gc.collect();mx.clear_cache()
        finally:
            mx.set_memory_limit(previous_memory)
            mx.set_cache_limit(previous_cache)
