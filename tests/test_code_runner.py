"""Coding agent runner: success, per-task cap, runaway spend, timeout, cancel, one at a time, daily budget, and a real seatbelt escape attempt.
A fake `claude` script stands in for the CLI so nothing costs money."""

import stat
import textwrap
import time
import unittest

import julius.agents.code_agent as ca
from julius.costs import CostTracker
from tests.test_code_agent import SEATBELT, Base

FAKE = textwrap.dedent("""\
    #!/opt/miniconda3/envs/torch/bin/python
    import json, os, sys, time
    argv = sys.argv; prompt = argv[argv.index("-p") + 1]; cap = float(argv[argv.index("--max-budget-usd") + 1])
    def emit(**e): print(json.dumps(e), flush=True)
    emit(type="system", subtype="init")
    if "FIXIT" in prompt:
        open("app.py", "w").write("def add(a, b):\\n    return a + b\\n"); emit(type="assistant", message={"content": "editing"})
        emit(type="result", subtype="success", is_error=False, result="I fixed add so the test passes. Tests pass.", total_cost_usd=0.42); sys.exit(0)
    if "SPEND" in prompt:
        emit(type="result", subtype="error_max_budget_usd", is_error=True, result="Budget reached.", total_cost_usd=cap); sys.exit(1)
    if "RUNAWAY" in prompt:
        emit(type="assistant", total_cost_usd=cap * 3); time.sleep(60); sys.exit(0)
    if "SLOW" in prompt:
        time.sleep(60); sys.exit(0)
    if "CRASH" in prompt:
        sys.exit(2)
    if "ESCAPE" in prompt:
        target = prompt.split("ESCAPE ", 1)[1].split()[0]
        try: open(target, "w").write("pwned"); wrote = True
        except OSError: wrote = False
        emit(type="result", subtype="success", is_error=False, result=f"wrote={wrote}", total_cost_usd=0.01); sys.exit(0)
    emit(type="result", subtype="success", is_error=False, result="nothing to do", total_cost_usd=0.01)
""")


class RunnerBase(Base):
    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake_claude"
        self.fake.write_text(FAKE)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self.tracker = CostTracker(self.tmp / "costs.jsonl", 5.0)

    def go(self, goal, *, cap=None, sandbox=False, timeout=None, **kw):
        t = ca.create_task(goal, str(self.proj), cap_usd=cap, config=self.cfg)
        if timeout:
            t.timeout_s = timeout
        done = []
        run = ca.start(
            t,
            api_key="sk-test",
            tracker=self.tracker,
            config=self.cfg,
            claude=str(self.fake),
            sandbox=sandbox,
            on_done=done.append,
            **kw,
        )
        return t, run, done


class RunnerTests(RunnerBase):
    def test_a_successful_task_changes_only_the_copy_and_records_its_cost(self):
        t, run, done = self.go("FIXIT make add work")
        self.assertTrue(run.wait(30))
        self.assertEqual(t.status, "done")
        self.assertEqual(t.cost_usd, 0.42)
        self.assertIn("a + b", (t.work / "app.py").read_text())
        self.assertIn("a - b", (self.proj / "app.py").read_text())
        self.assertIn("app.py", t.changes)
        self.assertEqual(done, [t])
        self.assertEqual(ca.load(t.id).status, "done")
        self.assertAlmostEqual(self.tracker.code_today(), 0.42)
        self.assertEqual(self.tracker.today_total(), 0.0)  # separate from the voice budget
        self.assertTrue((t.dir / "transcript.jsonl").read_text().count("\n") >= 2)
        text = ca.spoken_result(t)
        self.assertIn("$0.42", text)
        self.assertIn("merge it", text)

    def test_hitting_the_task_cap_is_reported_as_capped(self):
        t, run, _ = self.go("SPEND all of it", cap=0.5)
        run.wait(30)
        self.assertEqual(t.status, "capped")
        self.assertEqual(t.cost_usd, 0.5)
        self.assertIn("$0.50", ca.spoken_result(t))

    def test_runaway_spend_beyond_the_cap_is_killed(self):
        t, run, _ = self.go("RUNAWAY spend", cap=1.0)
        started = time.time()
        self.assertTrue(run.wait(30))
        self.assertLess(time.time() - started, 20)
        self.assertEqual(t.status, "capped")
        self.assertIsNotNone(run.proc.poll())

    def test_the_timeout_stops_a_stuck_task(self):
        t, run, _ = self.go("SLOW forever", timeout=2)
        started = time.time()
        self.assertTrue(run.wait(30))
        self.assertLess(time.time() - started, 15)
        self.assertEqual(t.status, "timeout")
        self.assertIn("minute", t.stop_reason + "minute")
        self.assertIsNotNone(run.proc.poll())

    def test_cancel_stops_the_task(self):
        t, run, _ = self.go("SLOW forever")
        time.sleep(1)
        self.assertTrue(ca.cancel())
        self.assertTrue(run.wait(15))
        self.assertEqual(t.status, "cancelled")
        self.assertFalse(ca.cancel())

    def test_only_one_task_runs_at_a_time(self):
        t, run, _ = self.go("SLOW forever")
        time.sleep(0.5)
        with self.assertRaises(ca.CodeProblem) as cm:
            self.go("FIXIT another")
        self.assertEqual(cm.exception.code, "busy")
        ca.cancel()
        run.wait(15)

    def test_the_daily_coding_budget_is_enforced_before_starting(self):
        self.tracker.record_usd("code", 4.0, "earlier")
        t = ca.create_task("FIXIT make add work", str(self.proj), cap_usd=1.5, config=self.cfg)
        with self.assertRaises(ca.CodeProblem) as cm:
            ca.start(
                t,
                api_key="sk-test",
                tracker=self.tracker,
                config=self.cfg,
                claude=str(self.fake),
                sandbox=False,
            )
        self.assertEqual(cm.exception.code, "daily_cap")
        self.assertEqual(t.status, "staged")

    def test_a_crash_is_a_failed_task_not_a_hang(self):
        t, run, done = self.go("CRASH now")
        self.assertTrue(run.wait(30))
        self.assertEqual(t.status, "failed")
        self.assertEqual(len(done), 1)
        self.assertIn("failed", ca.spoken_result(t))

    def test_a_missing_binary_ends_the_task_cleanly(self):
        t = ca.create_task("FIXIT make add work", str(self.proj), config=self.cfg)
        run = ca.start(
            t,
            api_key="sk-test",
            tracker=self.tracker,
            config=self.cfg,
            claude="/nonexistent/claude",
            sandbox=False,
        )
        self.assertTrue(run.wait(30))
        self.assertEqual(t.status, "failed")

    def test_no_key_and_restarting_a_finished_task_are_refused(self):
        t = ca.create_task("FIXIT make add work", str(self.proj), config=self.cfg)
        with self.assertRaises(ca.CodeProblem) as cm:
            ca.start(t, api_key="", config=self.cfg, claude=str(self.fake), sandbox=False)
        self.assertEqual(cm.exception.code, "no_key")
        t2, run, _ = self.go("FIXIT make add work")
        run.wait(30)
        with self.assertRaises(ca.CodeProblem) as cm:
            ca.start(t2, api_key="sk-test", config=self.cfg, claude=str(self.fake), sandbox=False)
        self.assertEqual(cm.exception.code, "not_staged")

    @unittest.skipUnless(SEATBELT, "needs macOS sandbox-exec")
    def test_inside_the_real_sandbox_the_agent_can_edit_its_copy_but_not_the_project(self):
        t, run, _ = self.go(f"ESCAPE {self.proj}/pwned.txt", sandbox=True)
        self.assertTrue(run.wait(60), "sandboxed run hung")
        self.assertEqual(t.status, "done", t.stop_reason)
        self.assertIn("wrote=False", t.summary)
        self.assertFalse((self.proj / "pwned.txt").exists())
        t2, run2, _ = self.go("FIXIT make add work", sandbox=True)
        run2.wait(60)
        self.assertIn("a + b", (t2.work / "app.py").read_text())
        self.assertEqual(t2.status, "done", t2.stop_reason)


class ConfigTests(unittest.TestCase):
    def test_shipped_code_config_is_valid_and_the_hard_max_holds(self):
        from julius import config

        c = config.load_config()["code"]
        self.assertEqual(
            (c["default_cap_usd"], c["max_cap_usd"], c["timeout_minutes"], c["model"]),
            (1.5, 5.0, 20, "claude-opus-5-5"),
        )


if __name__ == "__main__":
    unittest.main()
