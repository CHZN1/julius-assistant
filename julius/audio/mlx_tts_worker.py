"""Isolated streaming MLX Qwen worker; bounded files, stable reference prompts."""

import contextlib
import gc
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from julius import state
from julius.paths import REPO_ROOT

ROOT = REPO_ROOT
os.environ.setdefault("HF_HOME", str(ROOT / ".cache/huggingface-mlx-tts"))


def _model_is_cached(repo: str) -> bool:
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo, local_files_only=True)
        return True
    except Exception:
        return False


# Once the voice model is on disk the worker never touches the network; the first run downloads it.
if _model_is_cached("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

from typing import Never

import mlx.core as mx
import numpy as np

from julius.audio.mlx_voice_support import (
    load_reference_prompts,
    load_voice_model,
    normalize_speech,
)
from julius.audio.voice_identity import REF_TEXT, delivery_mode, speech_chunks

mx.set_cache_limit(256 * 1024**2)
mx.set_memory_limit(5 * 1024**3)


def load_models() -> Any:
    with contextlib.redirect_stdout(sys.stderr):
        model = load_voice_model(decoder_only=True)
        refs = load_reference_prompts(model)
        model.speech_tokenizer.encoder_model = None
        gc.collect()
        mx.clear_cache()
    return model, refs


model, refs = load_models()


def terminate_worker(signum: Any, frame: Any) -> Never:
    raise SystemExit(0)


signal.signal(signal.SIGTERM, terminate_worker)
cancel_flag = [False]
signal.signal(
    signal.SIGUSR1, lambda signum, frame: cancel_flag.__setitem__(0, True)
)  # the host abandoned this reply: stop generating, say 'done', stay alive
print(json.dumps({"ready": True}), flush=True)
with tempfile.TemporaryDirectory(prefix="julius_mlx_tts_", dir=state.tmp_dir()) as directory:
    for line in sys.stdin:
        req = json.loads(line)
        if req.get("op") == "close":
            break
        if req.get("op") == "unload":
            mx.synchronize()
            model = refs = generator = result = None
            gc.collect()
            mx.clear_cache()
            print(
                json.dumps({"unloaded": True, "active_memory_bytes": mx.get_active_memory()}),
                flush=True,
            )
            continue
        count = 0
        started = time.perf_counter()
        cancel_flag[0] = False
        try:
            text = req["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 4096:
                raise ValueError("Speech requires 1–4096 characters")
            if model is None:
                model, refs = load_models()
            text = normalize_speech(text)
            Path(directory).mkdir(
                parents=True, exist_ok=True
            )  # something outside (a temp cleaner) removed it once and every later reply went silent
            for path in Path(directory).glob("*.npy"):
                path.unlink()
            mode = req.get("delivery_mode") or delivery_mode(text)
            if mode not in refs:
                raise ValueError("Unknown delivery mode")
            config = model.generate_config
            span_words = int(req.get("span_words", 36))
            if not 12 <= span_words <= 48:
                raise ValueError("Speech span limit must be between 12 and 48 words")
            for span in speech_chunks(text, max_words=span_words):
                if cancel_flag[0]:
                    break
                with contextlib.redirect_stdout(sys.stderr):
                    generator = model._generate_icl(
                        text=span,
                        ref_audio=refs[mode],
                        ref_text=REF_TEXT,
                        language="English",
                        temperature=config["temperature"],
                        top_k=config["top_k"],
                        top_p=config["top_p"],
                        repetition_penalty=config["repetition_penalty"],
                        max_tokens=min(512, 24 + 12 * len(span.split())),
                        stream=True,
                        streaming_interval=0.32,
                    )
                    for result in generator:
                        audio = np.asarray(result.audio, dtype=np.float32)
                        if not audio.size or not np.isfinite(audio).all():
                            raise FloatingPointError("Invalid MLX speech audio")
                        path = Path(directory) / f"{count}.npy"
                        np.save(path, audio)
                        count += 1
                        print(
                            json.dumps(
                                {
                                    "chunk": True,
                                    "path": str(path),
                                    "sample_rate": result.sample_rate,
                                }
                            ),
                            file=sys.__stdout__,
                            flush=True,
                        )
                        if cancel_flag[0]:
                            break
            if cancel_flag[0] and model is not None:
                model.speech_tokenizer.decoder.reset_streaming_state()
            print(
                json.dumps(
                    {
                        "done": True,
                        "cancelled": cancel_flag[0],
                        "delivery_mode": mode,
                        "voice_strategy": "two-prompt",
                        "dtype": "bfloat16",
                        "chunks": count,
                        "seconds": time.perf_counter() - started,
                        "active_memory_bytes": mx.get_active_memory(),
                        "peak_memory_bytes": mx.get_peak_memory(),
                    }
                ),
                flush=True,
            )
        except Exception as exc:
            import traceback

            traceback.print_exc(file=sys.stderr)
            if model is not None:
                model.speech_tokenizer.decoder.reset_streaming_state()
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
        finally:
            mx.clear_cache()
