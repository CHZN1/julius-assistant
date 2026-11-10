"""Unit tests for mlx loader."""

import sys
import types
import unittest
from unittest.mock import Mock, patch

from julius.audio.mlx_voice_support import load_voice_model


class CachedLoaderTests(unittest.TestCase):
    def test_decoder_only_filters_encoder_weights_and_restores_hooks(self):
        class Decoder:
            pass

        class Tokenizer:
            def __init__(self, config):
                self.config = config

            @staticmethod
            def sanitize(weights):
                return weights

        architecture = types.SimpleNamespace(Qwen3TTSSpeechTokenizer=Tokenizer)
        config = types.SimpleNamespace(encoder_config=object(), decoder_config=object())
        original_encoder = config.encoder_config

        def load(_):
            tokenizer = architecture.Qwen3TTSSpeechTokenizer(config)
            self.assertIsNone(tokenizer.config.encoder_config)
            self.assertIs(tokenizer.config.decoder_config, config.decoder_config)
            self.assertEqual(
                tokenizer.sanitize({"encoder_model.w": 1, "decoder.w": 2}), {"decoder.w": 2}
            )
            return object()

        mx = types.SimpleNamespace(compile=Mock())
        compile_original = mx.compile
        modules = {
            "mlx": types.SimpleNamespace(core=mx),
            "mlx.core": mx,
            "mlx_audio.tts.utils": types.SimpleNamespace(load_model=load),
            "mlx_audio.tts.models.qwen3_tts": types.SimpleNamespace(qwen3_tts=architecture),
            "mlx_audio.tts.models.qwen3_tts.speech_tokenizer": types.SimpleNamespace(
                Qwen3TTSSpeechTokenizerDecoder=Decoder
            ),
        }
        with (
            patch.dict(sys.modules, modules),
            patch("julius.audio.mlx_voice_support.bound_decoder"),
        ):
            load_voice_model(decoder_only=True)
            self.assertIs(architecture.Qwen3TTSSpeechTokenizer, Tokenizer)
            self.assertIs(mx.compile, compile_original)
            self.assertIs(config.encoder_config, original_encoder)
            modules["mlx_audio.tts.utils"].load_model = Mock(
                side_effect=RuntimeError("bad checkpoint")
            )
            with self.assertRaises(RuntimeError):
                load_voice_model(decoder_only=True)
            self.assertIs(architecture.Qwen3TTSSpeechTokenizer, Tokenizer)
            self.assertIs(mx.compile, compile_original)
