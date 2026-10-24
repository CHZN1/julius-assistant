"""Cost per tier from usage (including cache pricing), the daily total, and the spend cap."""
import tempfile, unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import julius_costs as jc

def usage(i=0,o=0,w=0,r=0):return SimpleNamespace(input_tokens=i,output_tokens=o,cache_creation_input_tokens=w,cache_read_input_tokens=r)
class RateTests(unittest.TestCase):
 def test_haiku_rates_for_each_token_kind(self):
  # per million tokens at the up-to-100K-prompt card, scaled to 100K tokens so each case stays on that card
  self.assertAlmostEqual(jc.cost_of('haiku',usage(i=100_000)),0.10/10)
  self.assertAlmostEqual(jc.cost_of('haiku',usage(o=1_000_000)),0.50)
  self.assertAlmostEqual(jc.cost_of('haiku',usage(w=100_000)),0.125/10)
  self.assertAlmostEqual(jc.cost_of('haiku',usage(r=100_000)),0.01/10)
 def test_sonnet_rates_for_each_token_kind(self):
  self.assertAlmostEqual(jc.cost_of('sonnet',usage(i=1_000_000)),2.00)
  self.assertAlmostEqual(jc.cost_of('sonnet',usage(o=1_000_000)),10.00)
  self.assertAlmostEqual(jc.cost_of('sonnet',usage(w=1_000_000)),2.50)
  self.assertAlmostEqual(jc.cost_of('sonnet',usage(r=1_000_000)),0.10)
 def test_haiku_long_prompt_uses_the_higher_rate_card_counting_cached_tokens(self):
  self.assertAlmostEqual(jc.cost_of('haiku',usage(i=1000,r=100_000,o=1000)),(1000*.5+100_000*.05+1000*2.5)/1e6)
 def test_dict_usage_and_missing_fields_work(self):
  self.assertAlmostEqual(jc.cost_of('haiku',{'input_tokens':2000,'output_tokens':100}),(2000*.1+100*.5)/1e6)
  self.assertEqual(jc.cost_of('sonnet',{}),0)
 def test_realistic_cached_turn(self):
  # 1500-token cached prefix read, 40 fresh tokens, 60 output on Haiku
  self.assertAlmostEqual(jc.cost_of('haiku',usage(i=40,r=1500,o=60)),(40*.1+1500*.01+60*.5)/1e6)
class TrackerTests(unittest.TestCase):
 def setUp(self):
  self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
  self.path=Path(self.dir.name)/'costs.jsonl';self.now=[datetime(2026,10,24,12).timestamp()]
 def tracker(self,cap=1.0):return jc.CostTracker(self.path,cap,clock=lambda:self.now[0])
 def test_turn_costs_split_by_tier_and_daily_total_accumulates(self):
  t=self.tracker();t.begin_turn()
  t.record('haiku',usage(i=100_000));t.record('sonnet',usage(o=100_000))
  self.assertEqual({k:round(v,6) for k,v in t.turn_costs().items()},{'haiku':.01,'sonnet':1.0})
  self.assertAlmostEqual(t.today_total(),1.01);t.begin_turn();self.assertEqual(t.turn_costs(),{})
  self.assertEqual(t.today_by_tier().keys(),{'haiku','sonnet'})
  self.assertIn('today $1.0100 of $1.00',t.status_line())
 def test_log_rows_have_tokens_tier_and_model_and_persist_across_restarts(self):
  t=self.tracker();t.begin_turn();t.record('haiku',usage(i=10,o=5,w=7,r=3),note='x')
  row=__import__('json').loads(self.path.read_text().splitlines()[0])
  self.assertEqual((row['tier'],row['model'],row['input'],row['output'],row['cache_write'],row['cache_read']),('haiku','claude-haiku-5-5',10,5,7,3))
  self.assertAlmostEqual(self.tracker().today_total(),t.today_total())
 def test_cap_reached_stops_being_true_on_a_new_day(self):
  t=self.tracker(cap=0.5);t.record('sonnet',usage(o=100_000));self.assertTrue(t.cap_reached())
  self.now[0]+=86400;self.assertFalse(t.cap_reached());self.assertEqual(t.today_total(),0)
 def test_offline_is_required_at_double_the_cap_and_resets_next_day(self):
  t=self.tracker(cap=0.5);t.record('sonnet',usage(o=60_000));self.assertTrue(t.cap_reached() and not t.offline_required())  # $0.60
  t.record('sonnet',usage(o=60_000));self.assertTrue(t.offline_required());self.assertIn('OFFLINE',t.status_line())
  self.now[0]+=86400;self.assertFalse(t.offline_required())
 def test_status_line_marks_the_cap_state(self):
  t=self.tracker(cap=0.5);self.assertNotIn('cap reached',t.status_line());t.record('sonnet',usage(o=60_000));self.assertIn('cap reached: Haiku only',t.status_line())
 def test_corrupt_lines_are_ignored(self):
  self.path.write_text('not json\n');self.assertEqual(self.tracker().today_total(),0)
if __name__=='__main__':unittest.main()
