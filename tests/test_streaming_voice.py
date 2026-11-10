"""Streaming protocol recovery, buffered IPC, and continuous output contracts."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from julius.audio.tts import SpeechEngine
from julius.runtime import speech
from tests.harness import make_session


class StreamingProtocolTests(unittest.TestCase):
    def test_reminder_normalization_preserves_body_and_reference_text(self):
        from julius.audio.mlx_voice_support import normalize_speech
        from julius.audio.voice_identity import REF_TEXT

        self.assertEqual(
            normalize_speech("Reminder: the user, check this."),
            "Here is your reminder. the user, check this.",
        )
        self.assertEqual(normalize_speech(REF_TEXT), REF_TEXT)
        self.assertEqual(
            normalize_speech("Your reminder: check this."), "Your reminder: check this."
        )

    def engine(self, messages):
        e = SpeechEngine.__new__(SpeechEngine)
        e.backend = "qwen-mlx"
        e._isolated_worker = True
        e.worker = Mock()
        e.worker.poll.return_value = None
        e.worker.stdin = io.StringIO()
        e._worker_line = Mock(side_effect=[json.dumps(m) for m in messages])
        e.suspend = Mock()
        return e

    def test_all_chunks_consumed_before_next_request(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "audio.npy"
            np.save(path, np.ones(8, dtype=np.float32))
            chunk = {"chunk": True, "path": str(path), "sample_rate": 24000}
            e = self.engine([chunk, chunk, {"done": True, "chunks": 2}])
            result = list(e.synthesize("Hello."))
            self.assertEqual(len(result), 2)
            self.assertEqual(e.worker_metrics["chunks"], 2)
            e.suspend.assert_not_called()

    def test_abandoned_stream_closes_worker(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "audio.npy"
            np.save(path, np.ones(8))
            e = self.engine([{"chunk": True, "path": str(path), "sample_rate": 24000}])
            gen = e.synthesize("Hello.")
            next(gen)
            gen.close()
            e.suspend.assert_called_once()

    def test_empty_and_failed_streams_are_not_success(self):
        for messages in [[{"done": True}], [{"error": "numerical failure"}]]:
            e = self.engine(messages)
            with self.assertRaises(RuntimeError):
                list(e.synthesize("Hello."))
            e.suspend.assert_called_once()

    def test_pipe_backlog_does_not_wait_for_another_os_read(self):
        e = SpeechEngine.__new__(SpeechEngine)
        e.worker = Mock()
        e._worker_buffer = b""
        with (
            patch("select.select", return_value=([e.worker.stdout], [], [])) as poll,
            patch("os.read", return_value=b"first\nsecond\n") as read,
        ):
            self.assertEqual(e._worker_line(1), "first")
            self.assertEqual(e._worker_line(1), "second")
            self.assertEqual(read.call_count, 1)
            self.assertEqual(poll.call_count, 1)


class ContinuousPlaybackTests(unittest.TestCase):
    def speak(self, chunks):
        """Returns (speak function, sd mock, output mock) with a scripted voice and output device."""
        engine = Mock(backend="qwen-mlx")

        def generate():  # a real generator: the runtime closes it
            yield from chunks

        engine.synthesize.return_value = generate()
        sd = Mock()
        output = Mock()
        sd.OutputStream.return_value.__enter__ = Mock(return_value=output)
        sd.OutputStream.return_value.__exit__ = Mock(return_value=False)
        sd.PortAudioError = RuntimeError
        s = make_session(tts=engine, no_devices=False)

        def speak(text):
            with patch.object(speech, "sd", sd), patch.object(speech, "close_backend", Mock()):
                speech.speak_sentence(s, text)

        return speak, sd, output

    def test_one_output_stream_for_multiple_chunks(self):
        speak, sd, output = self.speak([(np.zeros(8), 24000), (np.ones(8), 24000)])
        speak("Hello.")
        sd.OutputStream.assert_called_once()
        self.assertEqual(output.write.call_count, 2)
        sd.play.assert_not_called()

    def test_rate_change_fails_instead_of_playing_at_wrong_rate(self):
        speak, _, _ = self.speak([(np.zeros(8), 24000), (np.ones(8), 16000)])
        with self.assertRaises(RuntimeError):
            speak("Hello.")


class PlayerRouteTests(unittest.TestCase):
    def test_speak_sentence_uses_the_persistent_player_when_present_and_notes_first_audio(self):
        from julius.audio.player import SpeechPlayer

        engine = Mock(backend="qwen-mlx")

        def generate():
            yield np.zeros(2400, dtype=np.float32), 24000
            yield np.ones(2400, dtype=np.float32), 24000

        engine.synthesize.return_value = generate()
        stream = Mock()
        stream.write.return_value = False
        sd = Mock()
        sd.OutputStream.return_value = stream
        player = SpeechPlayer(sd, idle_seconds=60, log=lambda *a, **k: None)
        s = make_session(tts=engine, no_devices=False, speech_player=player, response_started=1.0)
        with patch.object(speech, "close_backend", Mock()):
            speech.speak_sentence(s, "Hello.")
        sd.OutputStream.assert_called_once()
        self.assertEqual(stream.write.call_count, 3)
        self.assertIsNotNone(s.first_audio_at)
        player.close()


if __name__ == "__main__":
    unittest.main()
