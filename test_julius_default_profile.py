"""julius.py run directly (no arguments) must default to the cloud profile of run_julius.sh, never a local brain."""
import os, re, subprocess, sys, unittest
from pathlib import Path
import julius_profile as prof
HERE = Path(__file__).resolve().parent

class DefaultProfileTests(unittest.TestCase):
    def test_empty_environment_gets_cloud_brain_and_mlx_voice(self):
        env = prof.apply_cloud_defaults({})
        self.assertEqual((env["JULIUS_BACKEND"], env["JULIUS_MODEL"], env["JULIUS_TTS"]), ("cloud", "claude-haiku-5-5", "qwen-mlx"))
        self.assertFalse(any("qwen3:8b" in v for v in env.values()))
    def test_explicit_settings_win(self):
        env = prof.apply_cloud_defaults({"JULIUS_BACKEND": "ollama", "JULIUS_MODEL": "llama3.2:3b"})
        self.assertEqual((env["JULIUS_BACKEND"], env["JULIUS_MODEL"]), ("ollama", "llama3.2:3b"))
    def test_matches_run_julius_cloud_block(self):
        sh = (HERE / "run_julius.sh").read_text()
        block = sh[sh.index("  cloud)"):sh.index("  cloud-torch)")]
        profile = dict(re.findall(r"PROFILE_(BACKEND|MODEL|VOICE)=([^\s;]+)", block))
        self.assertEqual((profile["BACKEND"], profile["MODEL"], profile["VOICE"]), (prof.CLOUD_DEFAULTS["JULIUS_BACKEND"], prof.CLOUD_DEFAULTS["JULIUS_MODEL"], prof.CLOUD_DEFAULTS["JULIUS_TTS"]))
        for key, val in re.findall(r'export (JULIUS_\w+)="\$\{\1:-([^}]*)\}"', block):
            self.assertEqual(prof.CLOUD_DEFAULTS[key], val, key)
        self.assertIn('JULIUS_FALLBACK_MODEL="${JULIUS_FALLBACK_MODEL:-%s}"' % prof.CLOUD_DEFAULTS["JULIUS_FALLBACK_MODEL"], sh)
    def test_backend_and_candidate_default_to_cloud(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("JULIUS_")}
        code = "import julius_backend as b; print(b.LLM_BACKEND, b.LLM)"
        out = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True, env=env).stdout.split()
        self.assertEqual(out, ["cloud", "claude-haiku-5-5"])
    def test_help_prints_usage_and_exits_without_starting(self):
        r = subprocess.run([sys.executable, "julius.py", "--help"], cwd=HERE, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage: julius.py", r.stdout)
        self.assertNotIn("starting", (r.stdout + r.stderr).lower())

if __name__ == "__main__":
    unittest.main()
