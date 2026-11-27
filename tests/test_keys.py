"""Keys come from what you exported, then the Keychain, then the owner-only .env, and are never printed (julius/keys.py)."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from julius import config, keys


class Lookup(unittest.TestCase):
    def setUp(self):
        keys.reset()
        self.addCleanup(keys.reset)
        self.env = tempfile.mkdtemp()
        (Path(self.env) / ".env").write_text("SERPAPI_KEY=from-dotenv-123456\n")
        patches = [
            mock.patch.dict(
                os.environ,
                {"JULIUS_ENV_FILE": str(Path(self.env) / ".env"), "JULIUS_NO_KEYCHAIN": "0"},
            ),
            mock.patch.object(config, "DOTENV_LOADED", set()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("SERPAPI_KEY", None)

    def fake_security(self, items):
        def run(argv, **_):
            service = argv[argv.index("-s") + 1]
            if service in items:
                return SimpleNamespace(returncode=0, stdout=items[service] + "\n")
            return SimpleNamespace(returncode=44, stdout="")

        return mock.patch.object(keys.subprocess, "run", side_effect=run)

    def test_the_keychain_beats_dotenv(self):
        with self.fake_security({"julius:SERPAPI_KEY": "from-keychain-abcdef"}):
            self.assertEqual(keys.get("SERPAPI_KEY"), "from-keychain-abcdef")
            self.assertEqual(keys.source("SERPAPI_KEY"), "keychain")

    def test_dotenv_is_the_fallback(self):
        with self.fake_security({}):
            self.assertEqual(keys.get("SERPAPI_KEY"), "from-dotenv-123456")
            self.assertEqual(keys.source("SERPAPI_KEY"), ".env")

    def test_a_key_you_exported_wins(self):
        with (
            mock.patch.dict(os.environ, {"SERPAPI_KEY": "exported-for-this-run"}),
            self.fake_security({"julius:SERPAPI_KEY": "from-keychain-abcdef"}),
        ):
            self.assertEqual(keys.get("SERPAPI_KEY"), "exported-for-this-run")

    def test_the_older_anthropic_item_is_not_used(self):
        # it held a different (older) key than .env on 2026-11-26: switching to it silently could break the service
        with (
            mock.patch.dict(os.environ, {"JULIUS_ENV_FILE": "/nonexistent"}),
            self.fake_security({"julius-anthropic-key": "sk-ant-TEST-NOT-A-REAL-KEY"}),
        ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.assertIsNone(config.api_key())
            self.assertEqual(config.describe_key(), "missing")
        with (
            mock.patch.dict(os.environ, {"JULIUS_ENV_FILE": "/nonexistent"}),
            self.fake_security({"julius:ANTHROPIC_API_KEY": "sk-ant-TEST-NOT-A-REAL-KEY"}),
        ):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            keys.reset()
            self.assertEqual(config.api_key(), "sk-ant-TEST-NOT-A-REAL-KEY")

    def test_tests_never_read_the_real_keychain(self):
        with (
            mock.patch.dict(os.environ, {"JULIUS_NO_KEYCHAIN": "1"}),
            mock.patch.object(keys.subprocess, "run") as run,
        ):
            self.assertIsNone(keys.keychain("ANTHROPIC_API_KEY"))
        run.assert_not_called()
        import tests

        self.assertEqual(tests.ISOLATED["JULIUS_NO_KEYCHAIN"], "1")


class Helper(unittest.TestCase):
    def test_jkey_reads_from_the_keychain_and_stores_through_a_hidden_prompt(self):
        script = (Path(__file__).resolve().parents[1] / "scripts" / "jkey").read_text()
        self.assertIn(
            'security add-generic-password -U -a "$USER" -s "$(svc "$2")" -l "Julius: $2" -w\n',
            script,
        )  # no value on the command line
        self.assertIn("security find-generic-password", script)
        self.assertTrue(
            os.access(Path(__file__).resolve().parents[1] / "scripts" / "jkey", os.X_OK)
        )


if __name__ == "__main__":
    unittest.main()
