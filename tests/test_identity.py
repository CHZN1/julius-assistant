"""[identity]: one setting names the assistant everywhere it talks about itself; the default still says Julius."""

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import julius.agents.vision as vision
import julius.audio.listening as listening
import julius.brain.cloud as cloud
import julius.brain.conversation as conversation
import julius.brain.orchestrator as orchestrator
import julius.brain.session_state as session_state
import julius.config as cfg
import julius.conversations.manager as manager
import julius.memory.commands as memcommands
import julius.runtime.controls as controls
import julius.runtime.prompt as prompt
import julius.runtime.shutdown as shutdown
import julius.ui_bridge.events as ev
from julius import identity
from julius.wake import engine, phrases

AVA = identity.Identity(name="Ava")
# folder and file names stay the same on purpose (renaming never moves your data): see julius/identity.py
INFRA = ("Julius Saved", "Julius Notes", "Julius Art", "Julius Audio Workspace")


def without_infra(text):
    for keep in INFRA:
        text = text.replace(keep, "")
    return text


class Defaults(unittest.TestCase):
    def test_everything_is_julius_by_default(self):
        d = identity.Identity()
        self.assertEqual((d.name, d.display_name, d.wake_word), ("Julius", "Julius", "Julius"))
        self.assertEqual(d.model_stem, "julius")
        self.assertEqual(d.wake_words, ("Julius", "Juliet"))
        self.assertEqual(d.wake_contrast, cfg.WAKE_CONTRAST)
        self.assertEqual(identity.from_config({}), d)
        self.assertEqual(identity.from_config({"identity": {"name": "Julius"}}), d)

    def test_the_default_prompts_still_say_julius(self):
        self.assertTrue(prompt.BASE_PROMPT.startswith("You are Julius. Never describe yourself"))
        self.assertIn("you're Julius — the user built you", prompt.BASE_PROMPT)
        self.assertIn(
            "Named after Julius Novochrono — and you share his temperament. Warm, curious,",
            prompt.BASE_PROMPT,
        )
        self.assertTrue(
            conversation.CONVERSATION_SYSTEM.startswith("You are Julius, a local assistant")
        )
        self.assertIn(
            "planner behind Julius, a voice assistant. Julius's quick model",
            orchestrator.PLANNER_SYSTEM,
        )
        self.assertIn("Julius's own record of this session", cloud.PLANNING_PROMPT)
        self.assertTrue(vision.SYSTEM.startswith("You are Julius's eyes."))
        for text in identity.TEMPLATES:  # no token is ever left unfilled
            for token in ("{name}", "{display_name}", "{wake_word}", "{namesake_sentence}"):
                self.assertNotIn(token, identity.fill(text))

    def test_default_wake_matching_and_hint_are_unchanged(self):
        self.assertEqual(
            listening.WAKE_PREFIX.pattern,
            r"^\s*(?:(?:hey|okay|ok)[\s,]+)?(?:julius|juliet)\b[\s,.:!?-]*",
        )
        self.assertEqual(listening.WAKE_GLUED.pattern, r"^\s*(?i:julius|juliet)(?=[A-Z][a-z])")
        conf = {
            "asr": {"vocabulary_hint": True, "vocabulary": ["Julius"]},
            "profile": {"vocabulary": ["AcmeCorp"]},
        }
        self.assertEqual(cfg.asr_initial_prompt(conf), "Julius, AcmeCorp.")
        self.assertEqual(cfg.asr_initial_prompt(conf, wake=True), cfg.WAKE_CONTRAST + " AcmeCorp.")
        self.assertEqual(
            controls.sleep_line(mock.Mock(quiet_hotkey="<ctrl>+<alt>+j"), "go to sleep"),
            "Alright. Say Julius when you need me.",
        )
        self.assertEqual(
            ev.EventHub().hello()["identity"],
            {"name": "Julius", "display_name": "Julius", "wake_word": "Julius"},
        )

    def test_default_wake_model_is_the_shipped_starter(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(
            engine._starter_model(root).name, "julius_starter.onnx"
        )  # no check that it exists: the default never fails here


class Renamed(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(identity, "current", return_value=AVA)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_identity_fields_follow_the_name(self):
        self.assertEqual((AVA.display_name, AVA.wake_word, AVA.model_stem), ("Ava", "Ava", "ava"))
        self.assertEqual((AVA.namesake, AVA.wake_aliases), ("", ()))
        self.assertEqual(AVA.wake_contrast, "Ava. Hey Ava.")
        both = identity.Identity(
            name="Ava", display_name="Ava Desktop", wake_word="Nova", namesake="Ada Lovelace"
        )
        self.assertEqual(
            (both.display_name, both.wake_word, both.namesake),
            ("Ava Desktop", "Nova", "Ada Lovelace"),
        )
        self.assertIn("Named after Ada Lovelace", both.fill("{namesake_sentence}"))

    def test_every_prompt_template_drops_julius(self):
        self.assertGreaterEqual(len(identity.TEMPLATES), 9)
        for text in identity.TEMPLATES:
            filled = AVA.fill(text)
            self.assertNotIn("Julius", without_infra(filled), filled[:120])
        base = next(t for t in identity.TEMPLATES if t.startswith("You are {name}. Never"))
        self.assertTrue(AVA.fill(base).startswith("You are Ava. Never describe yourself"))
        self.assertNotIn("Novochrono", AVA.fill(base))
        self.assertIn(
            "\nWarm, curious,", AVA.fill(base)
        )  # the namesake sentence goes, the persona stays

    def test_spoken_replies_and_records_use_the_name(self):
        self.assertEqual(
            controls.sleep_line(mock.Mock(quiet_hotkey="<ctrl>+<alt>+j"), "go to sleep"),
            "Alright. Say Ava when you need me.",
        )
        session_state.reset()
        self.addCleanup(session_state.reset)
        session_state.record("sprite", "made a sprite of a cat")
        self.assertIn("Ava made 1 sprite", session_state.block())
        self.assertNotIn("Julius", session_state.block())
        self.assertIn("Ava: hi", manager._transcript([{"role": "assistant", "text": "hi"}]))
        with mock.patch.object(
            shutdown.jm, "_load", side_effect=lambda f: {"facts": [], "projects": []}
        ):
            p = shutdown.extraction_prompt([{"role": "assistant", "content": "done"}])
        self.assertIn("Ava: done", p)

    def test_wake_matching_follows_the_wake_word(self):
        prefix, glued = listening.wake_patterns(AVA)
        self.assertTrue(prefix.match("Hey Ava, what's the weather"))
        self.assertFalse(prefix.match("Hey Julius, what's the weather"))
        self.assertFalse(
            prefix.match("Juliet, are you there")
        )  # Julius's sound-alikes are not Ava's
        self.assertTrue(glued.match("AvaTrashReport.txt"))
        conf = {
            "asr": {"vocabulary_hint": True, "vocabulary": ["Julius"]},
            "profile": {"vocabulary": ["AcmeCorp"]},
            "identity": {"name": "Ava"},
        }
        self.assertEqual(cfg.asr_initial_prompt(conf), "Ava, AcmeCorp.")
        self.assertEqual(cfg.asr_initial_prompt(conf, wake=True), "Ava. Hey Ava. AcmeCorp.")

    def test_the_ui_gets_the_name(self):
        self.assertEqual(
            ev.EventHub().hello()["identity"],
            {"name": "Ava", "display_name": "Ava", "wake_word": "Ava"},
        )

    def test_a_new_wake_word_needs_its_own_model(self):
        root = Path(tempfile.mkdtemp())
        model = engine._starter_model(root)
        self.assertEqual(model.name, "ava_starter.onnx")
        self.assertFalse(model.exists())
        self.assertRegex(
            engine.missing_model_line(model),
            "no trained model for 'Ava'.*using the Whisper wake.*train_head",
        )
        said = []
        conf = {"wake": {"engine": "cascade", "model": str(model)}}
        self.assertIsNone(
            engine.from_config(lambda clip: True, None, conf, say=said.append)
        )  # falls back instead of failing
        self.assertTrue(said)
        (root / "julius" / "wake" / "models").mkdir(parents=True)
        (root / "julius" / "wake" / "models" / "ava_starter.onnx").write_bytes(b"")
        self.assertEqual(engine._starter_model(root).name, "ava_starter.onnx")

    def test_dataset_phrases_for_a_new_wake_word(self):
        p = phrases.for_wake("Ava")
        self.assertTrue(
            all("Julius" not in x for x in p.BARE + p.PREFIXES + p.JULIUS_OWN + p.JULIUS_OWN_EVAL)
        )
        self.assertIn("Hey Ava.", p.BARE)
        self.assertEqual(
            (p.SOUNDALIKES, p.SOUNDALIKE_SENTENCES), ([], [])
        )  # Julia and Julian are Julius's sound-alikes, not Ava's
        self.assertEqual(phrases.for_wake("Julius").BARE, phrases.BARE)


class Config(unittest.TestCase):
    def test_a_bad_name_is_refused_at_start_up(self):
        with self.assertRaisesRegex(ValueError, "identity.name"):
            identity.from_config({"identity": {"name": "<script>"}})
        with self.assertRaisesRegex(ValueError, "identity.wake_word"):
            identity.from_config({"identity": {"name": "Ava", "wake_word": "1234"}})

    def test_reading_julius_toml(self):
        with tempfile.TemporaryDirectory() as d:
            toml = Path(d) / "julius.toml"
            toml.write_text('[identity]\nname = "Ava"\ndisplay_name = "Ava Desktop"\n')
            with mock.patch.dict("os.environ", {"JULIUS_LOCAL_CONFIG": str(Path(d) / "none.toml")}):
                ident = identity.from_config(cfg.load_config(toml))
        self.assertEqual(
            (ident.name, ident.display_name, ident.wake_word), ("Ava", "Ava Desktop", "Ava")
        )


class ModulesStillImport(unittest.TestCase):
    def test_modules_that_fill_at_import_reload_cleanly(self):
        for mod in (
            prompt,
            conversation,
            orchestrator,
            cloud,
            vision,
            manager,
            shutdown,
            memcommands,
        ):
            importlib.reload(mod)
