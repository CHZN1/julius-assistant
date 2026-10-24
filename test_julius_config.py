"""Configuration defaults, overrides and key handling; the key value is never exposed."""
import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import julius_config as cfg

class ConfigTests(unittest.TestCase):
 def test_project_file_loads_with_expected_tiers_and_cap(self):
  c=cfg.load_config()
  self.assertEqual(c['models'],{'haiku':'claude-haiku-5-5','sonnet':'claude-sonnet-5-5'})
  self.assertEqual(c['brain']['mode'],'cloud');self.assertGreater(c['spend']['daily_cap_usd'],0)
  self.assertEqual(c['offline']['model'],'llama3.2:3b')
 def test_partial_file_keeps_other_defaults(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'j.toml';p.write_text('[spend]\ndaily_cap_usd = 1.5\n')
   c=cfg.load_config(p)
  self.assertEqual(c['spend']['daily_cap_usd'],1.5);self.assertEqual(c['orchestrator']['max_steps'],8)
 def test_environment_can_force_local_brain_and_invalid_values_fail(self):
  with patch.dict(os.environ,{'JULIUS_BRAIN':'local'}):self.assertEqual(cfg.load_config()['brain']['mode'],'local')
  with patch.dict(os.environ,{'JULIUS_BRAIN':'banana'}):
   with self.assertRaises(ValueError):cfg.load_config()
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'j.toml';p.write_text('[spend]\ndaily_cap_usd = 0\n')
   with self.assertRaises(ValueError):cfg.load_config(p)
 def test_dotenv_loads_without_overriding_and_key_is_not_described(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'.env';p.write_text('# c\nANTHROPIC_API_KEY="sk-test-secret-value"\nOTHER=1\n')
   with patch.dict(os.environ,{},clear=True):
    cfg.load_dotenv(p);self.assertEqual(os.environ['ANTHROPIC_API_KEY'],'sk-test-secret-value')
    with patch.dict(os.environ,{'JULIUS_ENV_FILE':str(p)}):
     self.assertNotIn('secret',cfg.describe_key());self.assertEqual(cfg.describe_key(),'set (20 chars)')
   with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'existing'},clear=True):
    cfg.load_dotenv(p);self.assertEqual(os.environ['ANTHROPIC_API_KEY'],'existing')
 def test_offline_multiple_defaults_to_two_and_must_exceed_one(self):
  self.assertEqual(cfg.load_config()['spend']['offline_at_multiple'],2.0)
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'j.toml';p.write_text('[spend]\noffline_at_multiple = 1\n')
   with self.assertRaises(ValueError):cfg.load_config(p)
 def test_missing_env_file_is_reported_as_missing_key(self):
  with patch.dict(os.environ,{'JULIUS_ENV_FILE':'/nonexistent/.env'},clear=True):
   self.assertEqual(cfg.describe_key(),'missing')
 def test_env_file_is_gitignored(self):
  import subprocess
  r=subprocess.run(['git','check-ignore','.env'],capture_output=True,text=True,cwd=cfg.ROOT)
  self.assertEqual(r.returncode,0)
if __name__=='__main__':unittest.main()
