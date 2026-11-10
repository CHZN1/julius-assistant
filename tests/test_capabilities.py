from julius.paths import REPO_ROOT

"""Capability prompt, backup tools (file_request, code_request) and the bare-yes reply: Julius must never decline something he can do or improvise an answer to a stray yes."""
import tempfile
import unittest
from pathlib import Path

import julius.brain.cloud as jc
import julius.brain.orchestrator as jo
import julius.brain.tools as jt
from julius.agents.file_agent import FileAgent
from julius.agents.file_session import NOTHING_WAITING, FileSession


class PromptTests(unittest.TestCase):
    def test_every_capability_is_named_so_haiku_never_declines_one(self):
        text = jc.CAPABILITIES_PROMPT
        for needle in (
            "file_request",
            "code_request",
            "remember_fact",
            "forget_fact",
            "list_memories",
            "set_reminder",
            "list_reminders",
            "cancel_reminder",
            "escalate_to_planner",
            "trash",
            "undo",
            "Never say you cannot",
            NOTHING_WAITING,
        ):
            self.assertIn(needle, text, needle)

    def test_every_user_facing_tool_is_in_the_catalogue_and_owned(self):
        from julius.brain.dispatcher import OWNERS

        names = {s["function"]["name"] for s in jt.SCHEMAS}
        for tool in ("file_request", "code_request", "remember_fact", "set_reminder"):
            self.assertIn(tool, names)
            self.assertIn(tool, OWNERS)
        self.assertTrue(all(n in jt.REGISTRY for n in ("file_request", "code_request")))

    def test_file_and_coding_requests_always_offer_their_tools(self):
        for said, tool in (
            ("get rid of the old budget file in the sandbox folder", "file_request"),
            ("put report.txt in the bin", "file_request"),
            ("undo that", "file_request"),
            ("fix the failing tests in demo calc", "code_request"),
            ("merge the changes", "code_request"),
        ):
            self.assertIn(tool, {s["function"]["name"] for s in jt.select_schemas(said)}, said)

    def test_the_production_system_prompt_includes_the_capabilities(self):
        source = (REPO_ROOT / "julius/runtime.py").read_text()
        self.assertIn("cloud.CAPABILITIES_PROMPT", source)


class BackupToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = (Path(self.temp.name) / "home").resolve()
        for d in ("Documents", "Downloads", "Desktop"):
            (self.root / d).mkdir(parents=True)
        (self.root / "Downloads" / "report.txt").write_text("r")
        self.agent = FileAgent(self.root, Path(self.temp.name) / "state")
        self.session = FileSession(self.agent)
        from julius.agents.code_session import CodeSession

        self.dev = self.root / "dev"
        (self.dev / "demo").mkdir(parents=True)
        (self.dev / "demo" / "a.py").write_text("x=1\n")
        self.code = CodeSession(
            config={"projects_root": str(self.dev)}, projects_root=str(self.dev), sandbox=False
        )
        import julius.agents.code_agent as _ca

        self._ca, self._old_home = _ca, _ca.CODE_HOME
        _ca.CODE_HOME = Path(self.temp.name) / "codehome"
        _ca.CODE_HOME.mkdir()  # never read the real task folders
        self.old = (jt.file_session, jt.code_session)
        jt.file_session, jt.code_session = self.session, self.code

    def tearDown(self):
        self._ca.CODE_HOME = self._old_home
        jt.file_session, jt.code_session = self.old
        jo._STATE["user_text"] = ""
        self.temp.cleanup()

    def say(self, text):
        jo._STATE["user_text"] = text

    def test_a_file_request_the_router_missed_reaches_the_file_controller_as_a_preview(self):
        self.say("could you possibly bin report.txt from downloads")
        out = jt.file_request("trash report.txt in Downloads")
        self.assertIn("report.txt", out)
        self.assertIn("Preview only", out)
        self.assertTrue((self.root / "Downloads" / "report.txt").exists())
        self.assertIsNotNone(self.session.pending)
        self.assertEqual(self.session.handle("yes", 5)["code"], "completed")
        self.assertFalse((self.root / "Downloads" / "report.txt").exists())

    def test_a_file_name_the_user_never_said_is_replaced_by_what_he_said(self):
        self.say("trash report.txt in Downloads")
        out = jt.file_request("trash secrets.txt in Downloads")
        self.assertIn("report.txt", out)
        self.assertNotIn("secrets", out)

    def test_an_unhandled_file_request_explains_instead_of_declining(self):
        self.say("do something with my files")
        out = jt.file_request("do something with my files")
        self.assertIn("Do not say you cannot", out)

    def test_a_coding_request_is_staged_only(self):
        self.say("can opus fix the bug in demo")
        out = jt.code_request(goal="fix the bug in a.py", project="demo")
        self.assertIn("coding task on demo", out)
        self.assertIn("Staged only", out)
        self.assertEqual(self.code.handle("cancel", 1)["code"], "cancelled")

    def test_coding_commands_without_a_task_say_what_to_do(self):
        self.say("merge it")
        self.assertIn("coding-agent", jt.code_request(request="merge it"))

    def test_without_sessions_the_tools_say_so(self):
        jt.file_session = jt.code_session = None
        self.assertIn("not available", jt.file_request("trash x.txt"))
        self.assertIn("not available", jt.code_request(goal="fix it please", project="demo"))


class BareYesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = (Path(self.temp.name) / "home").resolve()
        for d in ("Documents", "Downloads", "Desktop"):
            (root / d).mkdir(parents=True)
        (root / "Downloads" / "a.txt").write_text("a")
        self.a = root / "Downloads" / "a.txt"
        self.s = FileSession(FileAgent(root, Path(self.temp.name) / "state"))

    def tearDown(self):
        self.temp.cleanup()

    def test_a_bare_yes_with_nothing_waiting_gets_the_fixed_reply(self):
        for said in (
            "yes",
            "Yes.",
            "yeah",
            "do it",
            "go ahead",
            "yes please",
            "please do",
            "sure",
            "confirm",
        ):
            out = self.s.handle(said, 1)
            self.assertEqual(out["code"], "nothing_pending", said)
            self.assertEqual(out["message"], NOTHING_WAITING)

    def test_a_yes_that_answers_julius_own_question_is_left_to_the_conversation(self):
        self.s.suppress_idle_yes = True
        self.assertIsNone(self.s.handle("yes", 1))

    def test_okay_and_ordinary_sentences_are_not_treated_as_a_yes(self):
        for said in (
            "okay",
            "yes the weather is nice",
            "do it tomorrow at noon",
            "what time is it",
        ):
            self.assertIsNone(self.s.handle(said, 1), said)

    def test_a_waiting_plan_still_gets_its_yes_and_a_dropped_plan_gets_the_fixed_reply(self):
        self.assertEqual(self.s.handle("trash a.txt in Downloads", 1)["code"], "preview")
        self.assertEqual(self.s.handle("yes", 2)["code"], "completed")
        self.assertFalse(self.a.exists())
        self.s.handle("undo", 3)
        self.assertEqual(self.s.handle("trash a.txt in Downloads", 4)["code"], "preview")
        self.assertIsNone(self.s.handle("what's the weather", 5))
        self.assertEqual(self.s.handle("yes", 6)["message"], NOTHING_WAITING)
        self.assertTrue(self.a.exists())


if __name__ == "__main__":
    unittest.main()
