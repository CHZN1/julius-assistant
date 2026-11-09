"""[profile]: neutral defaults, a gitignored local file for personal values, and the prompts/tools that follow it."""
import os, tempfile, unittest
from pathlib import Path
import julius_config as cfg, julius_profile as jp, julius_cloud as jc, julius_tools as jt, julius_speech_text as st

def with_local(text):
    d = tempfile.mkdtemp(); p = Path(d) / "local.toml"; p.write_text(text)
    old = os.environ.get("JULIUS_LOCAL_CONFIG"); os.environ["JULIUS_LOCAL_CONFIG"] = str(p)
    def restore():
        if old is None: os.environ.pop("JULIUS_LOCAL_CONFIG", None)
        else: os.environ["JULIUS_LOCAL_CONFIG"] = old
    return restore

class Neutral(unittest.TestCase):
    def setUp(self): self.addCleanup(with_local("# empty\n"))
    def test_defaults_name_nobody(self):
        c = cfg.load_config(); self.assertEqual(c["profile"], {"name": "", "home_city": "", "vocabulary": []}); self.assertEqual(c["asr"]["vocabulary"], ["Julius"])
        self.assertEqual((c["asr"]["nhl_teams"], c["asr"]["nfl_teams"]), ([], []))
    def test_prompts_and_tools_carry_no_personal_names(self):
        text = jp.apply("the user built you. Address the user as you. the user's folders. Default is Springfield, Illinois.")
        for bad in ("the user", "Springfield"): self.assertNotIn(bad, text)
        self.assertIn("the user's folders", text)
        tools = jc._personalize_tools([{"name": "x", "description": "Omit location to use Springfield, Illinois.", "input_schema": {"properties": {"l": {"description": "ask the user"}}}}])
        self.assertNotIn("Springfield", str(tools)); self.assertNotIn("the user", str(tools))
    def test_no_home_city_asks_instead_of_guessing(self):
        self.assertIn("home city", jt.get_weather()); self.assertIn("home city", jt.get_forecast())
    def test_no_home_city_means_no_weather_shortcut(self):
        import julius_intents
        r = julius_intents.LiveIntentRouter(); self.assertIsNone(r.resolve("what's the weather"))
        self.assertEqual(r.resolve("what's the weather in Tokyo")[1], {"location": "Tokyo"})
    def test_acmecorp_fix_is_off_by_default(self):
        self.assertEqual(st.fix_transcript("price picks and N.H.L."), "price picks and NHL")

class Personal(unittest.TestCase):
    def setUp(self): self.addCleanup(with_local('[profile]\nname="Alex"\nhome_city="Reno, Nevada"\nvocabulary=["AcmeCorp"]\n'))
    def test_local_file_overrides_defaults(self):
        c = cfg.load_config(); self.assertEqual(c["profile"]["name"], "Alex")
        self.assertIn("AcmeCorp", cfg.asr_initial_prompt()); self.assertIn("Julius", cfg.asr_initial_prompt())
    def test_substitution_uses_the_profile(self):
        self.assertEqual(jp.apply("the user's Mac in Springfield, Illinois"), "Alex's Mac in Reno, Nevada")
    def test_home_city_is_the_weather_default(self):
        from unittest.mock import patch
        class R:
            def __init__(s, d): s.d = d
            def json(s): return s.d
        seen = []
        def fake(url, params=None, timeout=0):
            seen.append(params); return R({"results": []})
        with patch.object(jt.requests, "get", fake): jt.get_weather()
        self.assertEqual(seen[0]["name"], "Reno, Nevada")
    def test_acmecorp_fixed_when_listed(self):
        self.assertEqual(st.fix_transcript("price picks"), "AcmeCorp")

class LocalFileIsPrivate(unittest.TestCase):
    def test_gitignored(self):
        text = (Path(__file__).parent / ".gitignore").read_text(); self.assertIn("julius.local.toml", text)
if __name__ == "__main__": unittest.main()
