"""Native brain handoff and stream cancellation must release every model owner."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import julius.brain.backend as backend
from julius.runtime import boot
from julius.runtime.session import Session


class NativeCleanupTests(unittest.TestCase):
    def test_model_tokenizer_and_prompt_cache_release_together(self):
        mx = Mock()
        mx.get_active_memory.return_value = 0
        with (
            patch.dict("sys.modules", {"mlx": Mock(core=mx), "mlx.core": mx}),
            patch.multiple(
                backend,
                LLM_BACKEND="mlx",
                _BACKEND_USED=True,
                _MLX_MODEL=object(),
                _MLX_TOKENIZER=object(),
                _PROMPT_CACHE=object(),
            ),
        ):
            backend.close_backend()
            backend.close_backend()
            self.assertIsNone(backend._MLX_MODEL)
            self.assertIsNone(backend._MLX_TOKENIZER)
            self.assertIsNone(backend._PROMPT_CACHE)
            self.assertFalse(backend._BACKEND_USED)
            mx.synchronize.assert_called_once()
            mx.clear_cache.assert_called_once()
            self.assertEqual(backend.release_metrics()["active_memory_bytes"], 0)

    def test_sync_failure_retains_model_for_recovery(self):
        mx = Mock()
        mx.synchronize.side_effect = RuntimeError("GPU not synchronized")
        model = object()
        with (
            patch.dict("sys.modules", {"mlx": Mock(core=mx), "mlx.core": mx}),
            patch.multiple(backend, LLM_BACKEND="mlx", _BACKEND_USED=True, _MLX_MODEL=model),
        ):
            with self.assertRaises(RuntimeError):
                backend.close_backend()
            self.assertIs(backend._MLX_MODEL, model)
            self.assertTrue(backend._BACKEND_USED)


class StreamCleanupTests(unittest.TestCase):
    def test_actual_reply_stream_closes_on_cancelled_speech_callback(self):
        import contextlib
        import io

        from julius.audio.cancellation import TurnCancelled
        from tests.harness import TurnHarness

        h = TurnHarness()
        closed = []

        def stream():
            try:
                yield SimpleNamespace(message=SimpleNamespace(content="Hello. "))
                yield SimpleNamespace(message=SimpleNamespace(content="Another sentence."))
            finally:
                closed.append(True)

        h.llm_chat = lambda **kwargs: stream()

        def stop(_):
            raise TurnCancelled("stop")

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(TurnCancelled):
            h.ask("Tell me something.", "", speak_fn=stop)
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()


class PreparationTests(unittest.TestCase):
    def test_failed_preparation_still_unloads_before_voice_startup(self):
        with (
            patch.object(backend, "LLM_BACKEND", "mlx"),
            patch.object(backend, "llm_chat", side_effect=RuntimeError("failed prime")),
            patch.object(backend, "close_backend") as release,
        ):
            with self.assertRaises(RuntimeError):
                backend.prepare_backend()
            release.assert_called_once()


class StartupOrderTests(unittest.TestCase):
    def test_startup_primes_before_constructing_voice(self):
        events = []
        with (
            patch.dict(os.environ, {"JULIUS_MLX_BRAIN_PREPARE": "1"}),
            patch.object(boot, "create_vision") as vision,
            patch.object(boot, "prepare_backend", lambda: events.append("prime and release")),
            patch.object(boot, "SpeechEngine", lambda: events.append("voice") or Mock()),
            patch("builtins.print"),
        ):
            vision.return_value.start.return_value = Mock(enabled=False)
            s = Session()
            boot.setup_basics(s)
            boot.setup_vision(s)
            boot.setup_voice(s)
        self.assertEqual(events, ["prime and release", "voice"])


if __name__ == "__main__":
    unittest.main()
