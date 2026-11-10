"""The Ollama brain may stay resident across voice phases only while memory allows."""

import os
import unittest
from unittest.mock import patch

import julius.brain.backend as backend


class KeepBrainTests(unittest.TestCase):
    def test_kept_when_ollama_and_memory_is_free(self):
        with (
            patch.object(backend, "LLM_BACKEND", "ollama"),
            patch.object(backend, "_free_memory_percent", return_value=60),
        ):
            self.assertTrue(backend.keep_brain_loaded())

    def test_released_under_memory_pressure(self):
        with (
            patch.object(backend, "LLM_BACKEND", "ollama"),
            patch.object(backend, "_free_memory_percent", return_value=20),
        ):
            self.assertFalse(backend.keep_brain_loaded())

    def test_native_mlx_brain_always_releases(self):
        with (
            patch.object(backend, "LLM_BACKEND", "mlx"),
            patch.object(backend, "_free_memory_percent", return_value=90),
        ):
            self.assertFalse(backend.keep_brain_loaded())

    def test_environment_switch_restores_old_behaviour(self):
        with (
            patch.object(backend, "LLM_BACKEND", "ollama"),
            patch.object(backend, "_free_memory_percent", return_value=90),
            patch.dict(os.environ, {"JULIUS_KEEP_BRAIN": "0"}),
        ):
            self.assertFalse(backend.keep_brain_loaded())

    def test_unknown_pressure_releases(self):
        with (
            patch.object(backend, "LLM_BACKEND", "ollama"),
            patch.object(backend, "_FREE_CACHE", (0.0, 100.0)),
            patch("subprocess.check_output", side_effect=OSError),
        ):
            self.assertFalse(backend.keep_brain_loaded())


if __name__ == "__main__":
    unittest.main()
