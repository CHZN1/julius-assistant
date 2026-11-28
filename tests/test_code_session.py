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

    def settle(self, timeout=30):
        """Every run finished and its on_done callback delivered (on_done fires just after the run's done flag)."""
        for r in list(ca._RUNNING.values()):
            r.wait(timeout)
        end = time.time() + 5
        while not self.done and time.time() < end:
            time.sleep(0.02)

    def started(self):
        end = time.time() + 20
        while not any(r.proc for r in ca._RUNNING.values()) and time.time() < end:
            time.sleep(0.02)

    def run_task(self, goal="FIXIT make add work in demo"):
        self.assertEqual(self.s.handle(f"have the coding agent {goal}")["code"], "preview")
        out = self.s.handle("yes")
        self.assertEqual(out["code"], "started")
        self.settle()


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

    def test_a_polite_question_to_opus_stages_the_same_preview(self):
        out = self.s.handle("Can Opus fix FIXIT make the add function work in demo for me?")
        self.assertEqual(out["code"], "preview")
        self.assertEqual(ca.running(), [])

    def test_questions_to_opus_that_are_not_coding_requests_are_left_alone(self):
        self.assertIsNone(self.s.handle("Can Opus see me?"))
        self.assertEqual(ca.all_tasks(), [])

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

    def test_spoken_amounts_are_exact(self):
        for said, shown in (
            ("and limit it to 50 cents", "$0.50"),
            ("with half a dollar", "$0.50"),
            ("and spend a dollar fifty", "$1.50"),
            ("for 2 bucks", "$2.00"),
            ("limit it to $0.75", "$0.75"),
        ):
            out = self.s.handle(f"have the coding agent FIXIT make add work in demo {said}")
            self.assertEqual(out["code"], "preview", said)
            self.assertIn(f"at most {shown}", out["message"], said)
            self.assertEqual(out["data"]["project"], "demo", said)
            self.s.handle("cancel")

    def test_an_amount_with_no_unit_asks_dollars_or_cents_and_stages_nothing(self):
        out = self.s.handle("have the coding agent FIXIT make add work in demo, limit it to 50")
        self.assertEqual(out["code"], "clarification")
        self.assertIn("$50.00 or 50 cents", out["message"])
        self.assertEqual(ca.all_tasks(), [])
        self.assertIn(
            "at most $0.50",
            self.s.handle(
                "have the coding agent FIXIT make add work in demo, limit it to 50 cents"
            )["message"],
        )

    def test_a_hyphenated_project_name_wins_over_a_shorter_one_inside_it(self):
        for n in ("julius", "julius-runas-demo"):
            (self.dev / n).mkdir()
            (self.dev / n / "calc.py").write_text("x = 1\n")
        for said in (
            "have the coding agent fix calc.py and limit it to 50 cents in julius-runas-demo",
            "have the coding agent fix calc.py in julius-runas-demo, limit it to 50 cents",
            "have the coding agent fix calc.py in julius-runas-demo please",
            "have the coding agent fix the julius-runas-demo tests",
            "have the coding agent fix the julius runas demo tests",
        ):
            out = self.s.handle(said)
            self.assertEqual(out.get("data", {}).get("project"), "julius-runas-demo", said)
            self.s.handle("cancel")
        out = self.s.handle("have the coding agent fix the julius tests")
        self.assertEqual(out.get("data", {}).get("project"), "julius")

    def test_an_exact_folder_name_beats_a_loose_one(self):
        for n in ("demo-calc", "demo_calc"):
            (self.dev / n).mkdir()
        self.assertEqual(self.s.match_project("demo-calc"), "demo-calc")
        self.assertEqual(self.s.match_project("Demo_Calc"), "demo_calc")
        self.assertIsNone(self.s.match_project("demo calc"))  # two folders fit equally: ask

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
        self.started()
        self.assertIn("still running", self.s.handle("how is the coding task going")["message"])
        self.assertEqual(self.s.handle("cancel the coding task")["code"], "cancelled")
        self.settle(15)
        self.assertEqual(self.done[0].status, "cancelled")

    def test_only_one_task_at_a_time_from_voice(self):
        self.s.handle("have the coding agent SLOW work in demo")
        self.s.handle("yes")
        self.started()
        self.assertEqual(self.s.handle("have the coding agent FIXIT again in demo")["code"], "busy")


class MergeTests(FlowBase):
    def test_merge_needs_a_preview_then_its_own_yes_and_can_be_undone(self):
        self.run_task()
        original = (self.proj / "app.py").read_text()
        show = self.s.handle("show the changes")
        self.assertEqual(show["code"], "changes")
        self.assertTrue(any("app.py" in line for line in show["data"]["lines"]))
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


class FixWhatIsOnScreenTests(FlowBase):
    """ "Read this error on my screen" then "fix it": the coding agent gets what was read, in the project that has the file named in it."""

    def setUp(self):
        super().setUp()
        from julius.agents import vision

        self.vision = vision
        self.addCleanup(vision.LAST_SCREEN.clear)

    def seen(self, answer, age=0):
        self.vision.LAST_SCREEN.clear()
        self.vision.LAST_SCREEN.update(
            t=time.time() - age, question="read this error on my screen", answer=answer
        )

    def test_fix_it_stages_a_task_in_the_project_with_the_file(self):
        (self.dev / "demo" / "export_tool.py").write_text("print(1)\n")
        self.seen(
            "Running python export_tool.py failed with a FileNotFoundError for ~/Exports/settings.json."
        )
        out = self.s.handle("Fix it")
        self.assertEqual(out["code"], "preview", out)
        self.assertIn("demo", out["message"])
        task = ca.all_tasks()[0]
        self.assertEqual((task.status, task.project.rstrip("/").split("/")[-1]), ("staged", "demo"))
        self.assertIn("FileNotFoundError", task.goal)
        self.assertIn("add a test", task.goal)
        self.assertEqual(ca.running(), [])  # starting still takes its own yes

    def test_without_a_recent_screen_look_fix_it_is_not_a_coding_command(self):
        self.seen("an error in export_tool.py", age=3600)
        self.assertIsNone(self.s.handle("fix it"))

    def test_the_phrases(self):
        for ok in (
            "fix it",
            "Can you fix that?",
            "okay fix the error",
            "please fix it for me",
            "fix that bug",
        ):
            self.assertTrue(cs.FIX_IT.match(ok.lower().rstrip("?.!")), ok)
        for no in ("fix my sleep schedule", "fix it later when you can and also email Sam"):
            self.assertFalse(cs.FIX_IT.match(no), no)
