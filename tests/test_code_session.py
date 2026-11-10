"""Coding agent voice flow and planner tool: stage -> separate yes -> run -> show -> merge preview -> separate yes -> undo; nothing runs or merges without its own yes."""

import time
import unittest

import julius.agents.code_agent as ca
import julius.agents.code_session as cs
import julius.brain.orchestrator as jo
from tests.test_code_runner import RunnerBase


class FlowBase(RunnerBase):
    def setUp(self):
        super().setUp()
        self.done = []
        self.s = cs.CodeSession(
            config=self.cfg,
            tracker=self.tracker,
            api_key=lambda: "sk-test",
            claude=str(self.fake),
            sandbox=False,
            on_done=self.done.append,
            projects_root=str(self.dev),
        )

    def tearDown(self):
        ca.cancel()
        [r.wait(10) for r in list(ca._RUNNING.values())]
        ca._RUNNING.clear()
        super().tearDown()

    def run_task(self, goal="FIXIT make add work in demo"):
        self.assertEqual(self.s.handle(f"have the coding agent {goal}")["code"], "preview")
        out = self.s.handle("yes")
        self.assertEqual(out["code"], "started")
        ca.running()[0].wait(30)
        time.sleep(0.2)


class StageTests(FlowBase):
    def test_a_command_stages_a_preview_and_runs_nothing(self):
        out = self.s.handle("ask the coding agent to FIXIT make the add function work in demo")
        self.assertEqual(out["code"], "preview")
        self.assertTrue(out["ok"])
        for needle in ("demo", "$1.50", "20 minutes", "copy", "Say yes to start"):
            self.assertIn(needle, out["message"])
        self.assertEqual(ca.running(), [])
        self.assertEqual([t.status for t in ca.all_tasks()], ["staged"])
        self.assertFalse(list(ca.all_tasks()[0].work.glob("*")))

    def test_the_yes_must_be_a_separate_turn(self):
        out = self.s.handle("have the coding agent FIXIT make add work in demo and yes start it")
        self.assertEqual(out["code"], "preview")
        self.assertEqual(ca.running(), [])

    def test_yes_with_nothing_pending_is_not_swallowed(self):
        self.assertIsNone(self.s.handle("yes"))

    def test_cancel_discards_the_staged_task(self):
        self.s.handle("have the coding agent FIXIT make add work in demo")
        self.assertEqual(self.s.handle("cancel")["code"], "cancelled")
        self.assertEqual(ca.running(), [])
        self.assertIsNone(self.s.handle("yes"))

    def test_an_unrelated_request_drops_the_question(self):
        self.s.handle("have the coding agent FIXIT make add work in demo")
        self.assertIsNone(self.s.handle("what time is it"))
        self.assertIsNone(self.s.handle("yes"))
        self.assertEqual(ca.running(), [])

    def test_long_garbled_speech_does_not_lose_the_start_question_but_a_clear_question_does(self):
        """Live regression (18:11): his own preview heard back as 18 garbled words went to Haiku and the next 'yes' found nothing pending."""
        self.s.handle("have the coding agent FIXIT make add work in demo")
        self.assertIsNone(
            self.s.handle(
                "airlines pay differhee tickets available online via Sending eval 5000 TF spices Amtrak tickets"
            )
        )
        self.assertEqual(self.s.handle("yes")["code"], "started")
        ca.cancel()
        [r.wait(10) for r in list(ca._RUNNING.values())]
        ca._RUNNING.clear()
        self.s.handle("have the coding agent FIXIT make add work in demo")
        self.s.pending_at -= cs.PENDING_TTL + 1
        self.assertIsNone(
            self.s.handle(
                "airlines pay differhee tickets available online via Sending eval 5000 TF spices Amtrak tickets"
            )
        )
        self.assertIsNone(self.s.handle("yes"))  # too old

    def test_spoken_status_questions_reach_the_running_task(self):
        self.s.handle("have the coding agent SLOW make add work in demo")
        self.s.handle("yes")
        for said in ("What's the status?", "How's it going?", "Is it done?", "How much longer?"):
            self.assertEqual(self.s.handle(said)["code"], "status", said)
        ca.cancel()

    def test_status_words_without_a_running_task_are_left_alone(self):
        for said in ("What's the status?", "How's it going?"):
            self.assertIsNone(self.s.handle(said))

    def test_a_spoken_budget_is_used_and_clamped(self):
        out = self.s.handle(
            "have the coding agent FIXIT make add work in demo with a 3 dollar limit"
        )
        self.assertIn("$3.00", out["message"])
        out = self.s.handle("have the coding agent FIXIT make add work in demo with 50 dollars")
        self.assertIn("$5.00", out["message"])
        self.assertIn("capped", out["message"])

    def test_an_unknown_or_missing_project_asks_which_one(self):
        out = self.s.handle("have the coding agent fix the failing tests")
        self.assertEqual(out["code"], "clarification")
        self.assertIn("demo", out["message"])
        self.assertEqual(
            self.s.handle("have the coding agent fix the failing tests in nowhere")["code"],
            "clarification",
        )

    def test_projects_outside_the_projects_folder_or_with_music_or_crypto_names_cannot_be_named(
        self,
    ):
        for n in ("Logic Sessions", "btc_bot", "drum_stems"):
            (self.dev / n).mkdir(exist_ok=True)
        for n in ("Logic Sessions", "btc bot", "drum stems", "Documents", "home"):
            out = self.s.handle(f"have the coding agent fix the bug in {n}")
            self.assertEqual(out["code"], "clarification", n)
        self.assertEqual(ca.all_tasks(), [])

    def test_a_spoken_project_name_matches_loosely(self):
        (self.dev / "demo_calc").mkdir()
        self.assertEqual(self.s.match_project("demo calc"), "demo_calc")
        self.assertEqual(self.s.match_project("Demo-Calc project"), "demo_calc")


class RunTests(FlowBase):
    def test_start_runs_in_the_background_then_announces(self):
        self.run_task()
        t = self.done[0]
        self.assertEqual(t.status, "done")
        self.assertIn("a + b", (t.work / "app.py").read_text())
        self.assertIn("a - b", (self.proj / "app.py").read_text())
        self.assertIn("The coding task finished", ca.spoken_result(t))

    def test_status_and_cancel_while_running(self):
        self.s.handle("have the coding agent SLOW work in demo")
        self.s.handle("yes")
        time.sleep(0.5)
        self.assertIn("still running", self.s.handle("how is the coding task going")["message"])
        self.assertEqual(self.s.handle("cancel the coding task")["code"], "cancelled")
        ca.running() and ca.running()[0].wait(15)
        self.assertEqual(self.done[0].status, "cancelled")

    def test_only_one_task_at_a_time_from_voice(self):
        self.s.handle("have the coding agent SLOW work in demo")
        self.s.handle("yes")
        time.sleep(0.3)
        self.assertEqual(self.s.handle("have the coding agent FIXIT again in demo")["code"], "busy")


class MergeTests(FlowBase):
    def test_merge_needs_a_preview_then_its_own_yes_and_can_be_undone(self):
        self.run_task()
        original = (self.proj / "app.py").read_text()
        show = self.s.handle("show the changes")
        self.assertEqual(show["code"], "changes")
        self.assertTrue(any("app.py" in l for l in show["data"]["lines"]))
        self.assertEqual((self.proj / "app.py").read_text(), original)
        pre = self.s.handle("merge it")
        self.assertEqual(pre["code"], "preview")
        self.assertIn("app.py", pre["message"])
        self.assertIn("lines added", pre["message"])
        self.assertEqual((self.proj / "app.py").read_text(), original)
        done = self.s.handle("yes")
        self.assertEqual(done["code"], "merged")
        self.assertIn("a + b", (self.proj / "app.py").read_text())
        undone = self.s.handle("undo the merge")
        self.assertEqual(undone["code"], "undone")
        self.assertEqual((self.proj / "app.py").read_text(), original)

    def test_declining_the_merge_changes_nothing(self):
        self.run_task()
        original = (self.proj / "app.py").read_text()
        self.s.handle("merge it")
        self.assertEqual(self.s.handle("no")["code"], "cancelled")
        self.assertEqual((self.proj / "app.py").read_text(), original)
        self.assertIsNone(self.s.handle("yes"))

    def test_a_project_edit_made_after_the_preview_is_not_overwritten(self):
        self.run_task()
        self.s.handle("merge it")
        (self.proj / "app.py").write_text("my own edit\n")
        out = self.s.handle("yes")
        self.assertEqual((self.proj / "app.py").read_text(), "my own edit\n")
        self.assertIn("skipped", out["message"])

    def test_discard_asks_first_then_removes_only_the_copy(self):
        self.run_task()
        t = self.done[0]
        pre = self.s.handle("discard the coding task")
        self.assertEqual(pre["code"], "preview")
        self.assertTrue(t.dir.exists())
        self.assertEqual(self.s.handle("yes")["code"], "discarded")
        self.assertFalse(t.dir.exists())
        self.assertTrue((self.proj / "app.py").exists())

    def test_merge_it_means_nothing_without_a_recent_coding_task(self):
        self.assertIsNone(self.s.handle("merge it"))
        self.assertIsNone(self.s.handle("show the changes"))
        self.run_task()
        t = self.done[0]
        t.finished = time.time() - 3 * 86400
        ca.save(t)
        self.s.task = None
        self.assertIsNone(self.s.handle("merge it"))


class PlannerToolTests(FlowBase):
    def setUp(self):
        super().setUp()
        jo.configure(code_session=self.s)

    def tearDown(self):
        jo._STATE["code_session"] = None
        super().tearDown()

    def test_the_planner_tool_only_stages(self):
        out = jo.code_task(
            {"goal": "FIXIT make add work", "project": "demo", "criteria": "test_app passes"}
        )
        self.assertIn("Staged only", out)
        self.assertEqual(ca.running(), [])
        self.assertEqual(self.s.handle("yes")["code"], "started")

    def test_the_planner_cannot_pick_a_protected_project_or_exceed_the_budget(self):
        (self.dev / "btc_bot").mkdir()
        self.assertNotIn(
            "Staged only", jo.code_task({"goal": "FIXIT whatever", "project": "btc_bot"})
        )
        self.assertIn(
            "$5.00",
            jo.code_task({"goal": "FIXIT make add work", "project": "demo", "budget_usd": 500}),
        )

    def test_without_a_session_the_tool_says_so_and_it_is_offered_to_the_planner(self):
        jo._STATE["code_session"] = None
        self.assertIn("not available", jo.code_task({"goal": "x" * 20, "project": "demo"}))
        self.assertIn(
            "code_task", [t["name"] for t in jo.Planner.tools(jo.Planner.__new__(jo.Planner))]
        )


if __name__ == "__main__":
    unittest.main()
