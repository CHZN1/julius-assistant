"""Audio analysis can decode unsupported formats without macOS or real audio tools."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from julius.music import analysis


class Decoding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "track with spaces.m4a"
        self.source.write_bytes(b"original audio")
        self.samples = [0.0] * analysis.SR
        self.librosa = mock.Mock()
        self.librosa.load.return_value = (self.samples, analysis.SR)
        self.soundfile = mock.Mock()
        self.info = SimpleNamespace(frames=analysis.SR, samplerate=analysis.SR)
        self.soundfile.info.side_effect = [RuntimeError("unsupported format"), self.info]
        modules = mock.patch.dict(
            "sys.modules", {"librosa": self.librosa, "soundfile": self.soundfile}
        )
        modules.start()
        self.addCleanup(modules.stop)

    def decode(self, cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"decoded audio")
        return SimpleNamespace(returncode=0)

    def test_ffmpeg_is_preferred_and_keeps_the_analysis_format(self):
        executables = {"ffmpeg": "/tools/ffmpeg", "afconvert": "/usr/bin/afconvert"}
        with (
            mock.patch.object(analysis.shutil, "which", side_effect=executables.get),
            mock.patch.object(analysis.subprocess, "run", side_effect=self.decode) as run,
        ):
            result = analysis.load(self.source)

        self.assertEqual(result, (self.samples, 1.0, 1.0))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "/tools/ffmpeg")
        for flag, value in (
            ("-i", str(self.source)),
            ("-ac", "1"),
            ("-ar", str(analysis.SR)),
            ("-c:a", "pcm_s16le"),
            ("-f", "wav"),
        ):
            self.assertEqual(cmd[cmd.index(flag) + 1], value)
        self.assertIn("-nostdin", cmd)
        self.assertEqual(run.call_args.kwargs, {"capture_output": True, "timeout": 120})
        decoded = Path(cmd[-1])
        self.assertNotEqual(decoded.parent, self.source.parent)
        self.librosa.load.assert_called_once_with(
            str(decoded), sr=analysis.SR, mono=True, offset=0.0, duration=analysis.MAX_SECONDS
        )
        self.assertFalse(decoded.exists(), "the temporary decoding copy is removed")
        self.assertEqual(self.source.read_bytes(), b"original audio")

    def test_afconvert_is_used_when_ffmpeg_is_missing(self):
        with (
            mock.patch.object(
                analysis.shutil, "which", side_effect={"afconvert": "/tools/afconvert"}.get
            ),
            mock.patch.object(analysis.subprocess, "run", side_effect=self.decode) as run,
        ):
            analysis.load(self.source)

        self.assertEqual(
            run.call_args.args[0][:-1],
            [
                "/tools/afconvert",
                "-f",
                "WAVE",
                "-d",
                f"LEI16@{analysis.SR}",
                "-c",
                "1",
                str(self.source),
            ],
        )

    def test_missing_converters_raise_a_speakable_error(self):
        with (
            mock.patch.object(analysis.shutil, "which", return_value=None),
            mock.patch.object(analysis.subprocess, "run", side_effect=self.decode) as run,
            self.assertRaisesRegex(analysis.AudioProblem, "install ffmpeg"),
        ):
            analysis.load(self.source)
        run.assert_not_called()

    def test_failed_decode_raises_audio_problem(self):
        with (
            mock.patch.object(analysis.shutil, "which", return_value="/tools/ffmpeg"),
            mock.patch.object(
                analysis.subprocess, "run", return_value=SimpleNamespace(returncode=1)
            ),
            self.assertRaisesRegex(analysis.AudioProblem, "couldn't read the audio"),
        ):
            analysis.load(self.source)

    def test_missing_decoded_output_raises_audio_problem(self):
        with (
            mock.patch.object(analysis.shutil, "which", return_value="/tools/ffmpeg"),
            mock.patch.object(
                analysis.subprocess, "run", return_value=SimpleNamespace(returncode=0)
            ),
            self.assertRaisesRegex(analysis.AudioProblem, "couldn't read the audio"),
        ):
            analysis.load(self.source)

    def test_readable_audio_does_not_need_a_converter(self):
        self.soundfile.info.side_effect = None
        self.soundfile.info.return_value = self.info
        with mock.patch.object(analysis.subprocess, "run") as run:
            self.assertEqual(analysis.load(self.source), (self.samples, 1.0, 1.0))
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
