"""Configuration defaults, overrides and key handling; the key value is never exposed."""
import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import julius.config as cfg

class ConfigTests(unittest.TestCase):
 def setUp(self):
  from tests import profile_fixture;profile_fixture.personal(self)
 def test_asr_vocabulary_hint_is_built_from_config_and_can_be_disabled(self):
  on=cfg.load_config();on['asr']['vocabulary_hint']=True
  hint=cfg.asr_initial_prompt(on)
  for word in ('AcmeCorp','Pat Example','FER 2013','Ethereum','Sharks','Chiefs'):self.assertIn(word,hint)
  self.assertLess(len(hint.split()),150)  # Whisper reads at most 224 prompt tokens
  c=cfg.load_config();c['asr']['vocabulary_hint']=False;self.assertIsNone(cfg.asr_initial_prompt(c))
 def test_hint_names_the_wake_word_with_its_sound_alikes_and_never_lists_julius_alone(self):
  conf=dict(cfg.load_config(),asr=dict(cfg.load_config()['asr'],vocabulary_hint=True,vocabulary=['Julius','AcmeCorp']))
  self.assertTrue(cfg.asr_initial_prompt(conf).startswith('Julius, AcmeCorp'));self.assertNotIn('Julia',cfg.asr_initial_prompt(conf))  # awake request hint: vocabulary only
  hint=cfg.asr_initial_prompt(conf,wake=True)
  self.assertTrue(hint.startswith(cfg.WAKE_CONTRAST));self.assertIn('Julia. Julian.',hint);self.assertNotIn(', Julius',hint);self.assertIn('AcmeCorp',hint)
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
