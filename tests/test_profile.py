"""[profile]: neutral defaults, a gitignored local file for personal values, and the prompts/tools that follow it."""

import os
import tempfile
import unittest
from pathlib import Path

import julius.audio.speech_text as st
import julius.brain.cloud as jc
import julius.brain.tools as jt
import julius.config as cfg
import julius.profile as jp
from julius.paths import REPO_ROOT


def with_local(text):
    d = tempfile.mkdtemp()
    p = Path(d) / "local.toml"
    p.write_text(text)
    old = os.environ.get("JULIUS_LOCAL_CONFIG")
    os.environ["JULIUS_LOCAL_CONFIG"] = str(p)

    def restore():
        if old is None:
            os.environ.pop("JULIUS_LOCAL_CONFIG", None)
        else:
            os.environ["JULIUS_LOCAL_CONFIG"] = old

    return restore


class Neutral(unittest.TestCase):
    def setUp(self):
        self.addCleanup(with_local("# empty\n"))

    def test_defaults_name_nobody(self):
        c = cfg.load_config()
        self.assertEqual(c["profile"], {"name": "", "home_city": "", "vocabulary": []})
        self.assertEqual(c["asr"]["vocabulary"], ["Julius"])
        self.assertEqual((c["asr"]["nhl_teams"], c["asr"]["nfl_teams"]), ([], []))

    def test_prompts_and_tools_carry_no_personal_names(self):
        text = jp.apply(
            "the user built you. Address the user as you. The user's folders. Default is Springfield, Illinois."
        )
        self.assertNotIn("Springfield", text)
        self.assertIn("the user's home city", text)
        self.assertIn("The user's folders", text)
        tools = jc._personalize_tools(
            [
                {
                    "name": "x",
                    "description": "Omit location to use Springfield, Illinois.",
                    "input_schema": {"properties": {"l": {"description": "ask the user"}}},
                }
            ]
        )
        self.assertNotIn("Springfield", str(tools))

    def test_no_home_city_asks_instead_of_guessing(self):
        self.assertIn("home city", jt.get_weather())
        self.assertIn("home city", jt.get_forecast())

    def test_no_home_city_means_no_weather_shortcut(self):
        from julius.brain import intents

        r = intents.LiveIntentRouter()
        self.assertIsNone(r.resolve("what's the weather"))
        self.assertEqual(r.resolve("what's the weather in Tokyo")[1], {"location": "Tokyo"})

    def test_transcript_fixes_known_acronyms(self):
        self.assertEqual(st.fix_transcript("price picks and N.H.L."), "price picks and NHL")


class Personal(unittest.TestCase):
    def setUp(self):
        self.addCleanup(
            with_local(
                '[profile]\nname="Alex"\nhome_city="Reno, Nevada"\nvocabulary=["AcmeCorp"]\n'
            )
        )

    def test_local_file_overrides_defaults(self):
        c = cfg.load_config()
        self.assertEqual(c["profile"]["name"], "Alex")
        self.assertIn("AcmeCorp", cfg.asr_initial_prompt())
        self.assertIn("Julius", cfg.asr_initial_prompt())

    def test_substitution_uses_the_profile(self):
        self.assertEqual(
            jp.apply("the user's Mac in Springfield, Illinois"), "Alex's Mac in Reno, Nevada"
        )

    def test_home_city_is_the_weather_default(self):
        from unittest.mock import patch

        class R:
            def __init__(s, d):
                s.d = d

            def json(s):
                return s.d

        seen = []

        def fake(url, params=None, timeout=0):
            seen.append(params)
            return R({"results": []})

        with patch.object(jt.requests, "get", fake):
            jt.get_weather()
        self.assertEqual(seen[0]["name"], "Reno, Nevada")


class LocalFileIsPrivate(unittest.TestCase):
    def test_gitignored(self):
        text = (REPO_ROOT / ".gitignore").read_text()
        self.assertIn("julius.local.toml", text)


if __name__ == "__main__":
    unittest.main()


class FirstRunSetup(unittest.TestCase):
    def test_setup_writes_a_profile_and_never_overwrites_one(self):
        from pathlib import Path

        self.addCleanup(with_local(""))
        target = Path(os.environ["JULIUS_LOCAL_CONFIG"])
        target.unlink()
        answers = iter(["Alex", "Reno, Nevada", "Sharks, Kraken"])
        said = []
        self.assertEqual(jp.setup(lambda _q: next(answers), said.append), target)
        c = cfg.load_config()
        self.assertEqual(c["profile"]["name"], "Alex")
        self.assertEqual(c["profile"]["vocabulary"], ["Sharks", "Kraken"])
        self.assertFalse(jp.needs_setup())
        self.assertIsNone(jp.setup(lambda _q: "x", said.append))
        self.assertIn("already has", said[-1])


class VisionHook(unittest.TestCase):
    def test_vision_is_off_without_a_module_and_enabled_vision_needs_one(self):
        import os

        from julius.brain.vision_mode import VisionMode

        self.assertFalse(VisionMode(enabled=False).enabled)
        os.environ["JULIUS_VISION_MODULE"] = "no_such_perception_module"
        self.addCleanup(os.environ.pop, "JULIUS_VISION_MODULE", None)
        with self.assertRaises(ImportError):
            VisionMode(enabled=True)


class ModelFetch(unittest.TestCase):
    def test_fetch_downloads_wake_speech_and_voice_models_into_the_workers_cache(self):
        import sys
        import types
        from unittest.mock import patch

        from julius import models

        calls = []
        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = lambda repo, **kw: calls.append((repo, kw.get("cache_dir")))
        oww = types.ModuleType("openwakeword.utils")
        oww.download_models = lambda: calls.append(("wake", None))
        pkg = types.ModuleType("openwakeword")
        pkg.utils = oww
        with patch.dict(
            sys.modules,
            {"huggingface_hub": hub, "openwakeword": pkg, "openwakeword.utils": oww},
        ):
            models.fetch(lambda _m: None)
        self.assertEqual([c[0] for c in calls], ["wake", models.WHISPER_REPO, models.VOICE_REPO])
        self.assertEqual(calls[2][1], models.voice_cache_dir())
