"""Bound MLX codec allocations while retaining decoder left context."""

import hashlib
import json
import re
from types import MethodType
from typing import Any


def normalize_speech(text: str) -> str:
    # MLX cloning can omit a label even with a comma. A complete spoken
    # introduction retains the reminder meaning and leaves its body intact.
    return re.sub(r"^Reminder:\s*", "Here is your reminder. ", text, flags=re.I)


def load_reference_prompts(model: Any) -> Any:
    import mlx.core as mx
    import numpy as np
    from mlx_audio.utils import load_audio

    from julius.audio.voice_identity import BALANCED_REFERENCE, LIFT_REFERENCE, REF_TEXT, ROOT

    refs = {}
    for mode, path in [("balanced", BALANCED_REFERENCE), ("lift", LIFT_REFERENCE)]:
        directory = ROOT / "julius_1301_tuning/codec_prompts"
        info = json.loads((directory / f"{mode}.json").read_text())
        if (
            info["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()
            or info["text"] != REF_TEXT
        ):
            raise ValueError("Cached voice prompt no longer matches selected Julius reference")
        raw = np.load(directory / f"{mode}.npy", allow_pickle=False)
        if raw.ndim != 2 or raw.shape[1] != 16 or not np.issubdtype(raw.dtype, np.integer):
            raise ValueError("Invalid cached Qwen codec prompt")
        refs[mode] = load_audio(str(path), sample_rate=24000)
        codes = mx.array(raw.T[None])
        ids = mx.array(model.tokenizer.encode(f"<|im_start|>assistant\n{REF_TEXT}<|im_end|>\n"))[
            None, 3:-2
        ]
        model._icl_cache[(REF_TEXT, (refs[mode].size, float(refs[mode].sum())))] = (codes, ids)
    return refs


def load_voice_model(decoder_only: bool = False) -> object:
    import mlx.core as mx
    from mlx_audio.tts.models.qwen3_tts import qwen3_tts as architecture
    from mlx_audio.tts.models.qwen3_tts.speech_tokenizer import Qwen3TTSSpeechTokenizerDecoder
    from mlx_audio.tts.utils import load_model

    original_tokenizer = architecture.Qwen3TTSSpeechTokenizer
    if decoder_only:
        # Runtime uses verified, complete reference codes. Do not construct or
        # materialize the unused waveform encoder just to discard it afterward.
        import copy

        class CachedReferenceTokenizer(original_tokenizer):
            def __init__(self, config: dict[str, Any]) -> None:
                config = copy.copy(config)
                config.encoder_config = None
                super().__init__(config)

            @staticmethod
            def sanitize(weights: Any) -> Any:
                return {
                    key: value
                    for key, value in original_tokenizer.sanitize(weights).items()
                    if not key.startswith("encoder_model.")
                }

        architecture.Qwen3TTSSpeechTokenizer = CachedReferenceTokenizer
    compile_original = mx.compile

    # MLX Audio 0.5.7 replaces this module with a callable, losing streaming
    # methods (and its parameter tree). Keep the loaded decoder module intact.
    def compile_preserving_decoder(fn: Any, *args, **kwargs) -> Any:
        if isinstance(fn, Qwen3TTSSpeechTokenizerDecoder):
            return fn
        return compile_original(fn, *args, **kwargs)

    mx.compile = compile_preserving_decoder
    try:
        model = load_model("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16")
    finally:
        mx.compile = compile_original
        architecture.Qwen3TTSSpeechTokenizer = original_tokenizer
    bound_decoder(model)
    return model


def bound_decoder(model: Any) -> None:
    import mlx.core as mx

    def decode(decoder: Any, codes: Any, chunk_size: int = 96, left_context_size: int = 25) -> Any:
        parts = []
        for start in range(0, codes.shape[-1], chunk_size):
            context = min(start, left_context_size)
            part = decoder(codes[..., start - context : min(start + chunk_size, codes.shape[-1])])
            part = part[..., context * decoder.total_upsample :]
            mx.eval(part)  # Materialize each chunk; avoid retaining all activation graphs.
            parts.append(part)
            mx.clear_cache()
        return mx.concatenate(parts, axis=-1)

    decoder = model.speech_tokenizer.decoder
    decoder.chunked_decode = MethodType(decode, decoder)
