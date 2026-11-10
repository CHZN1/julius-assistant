import concurrent.futures
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from julius.agents.utility_session import UtilitySession
from julius.brain.conversions import convert_units, date_calculate, local_datetime
from julius.brain.planning import Planner


class PlanningTests(unittest.TestCase):
    def test_announced_reminder_is_not_completed_until_human_command(self):
        out = self.s.handle("remind me in 1 second to water", 1)
        self.now[0] += 2
        self.s.deliver_due(Mock())
        with self.p.db() as con:
            self.assertEqual(
                con.execute("SELECT status FROM items WHERE id=?", (out["data"]["id"],)).fetchone()[
                    "status"
                ],
                "delivered",
            )
        self.assertTrue(self.s.handle("mark reminder water done", 2)["ok"])
        self.assertTrue(self.p.finish(out["data"]["id"], "done", "reminder")["ok"])
        self.assertEqual(self.s.deliver_due(Mock()), [])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [datetime(2026, 10, 1, tzinfo=UTC).timestamp()]
        self.p = Planner(self.tmp.name, clock=lambda: self.now[0])
        self.s = UtilitySession(self.p)

    def test_reminder_survives_restart_and_delivers_once_after_ack(self):
        out = self.s.handle("remind me in 5 minutes to drink water", 1)
        self.assertTrue(out["ok"])
        self.now[0] += 301
        p = Planner(self.tmp.name, clock=lambda: self.now[0])
        s = UtilitySession(p)
        speak = Mock()
        self.assertEqual(s.deliver_due(speak), [out["data"]["id"]])
        s.deliver_due(speak)
        speak.assert_called_once_with("Reminder: drink water")

    def test_failed_playback_is_retried(self):
        self.p.add("reminder", "water", self.now[0] + 1)
        self.now[0] += 2
        with self.assertRaises(RuntimeError):
            self.s.deliver_due(Mock(side_effect=RuntimeError("speaker offline")))
        speak = Mock()
        self.assertEqual(len(self.s.deliver_due(speak)), 1)

    def test_crash_lease_recovery_and_old_ack_rejected(self):
        self.p.add("reminder", "water", self.now[0] + 1)
        self.now[0] += 2
        first = self.p.claim_due()[0]
        self.assertEqual(self.p.claim_due(), [])
        self.now[0] += 121
        second = self.p.claim_due()[0]
        self.assertFalse(self.p.acknowledge(first["id"], first["lease"]))
        self.assertTrue(self.p.acknowledge(second["id"], second["lease"]))

    def test_concurrent_clients_claim_one_reminder_once(self):
        self.p.add("reminder", "water", self.now[0] + 1)
        self.now[0] += 2

        def claim(_):
            return Planner(self.tmp.name, clock=lambda: self.now[0]).claim_due()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(4)))
        self.assertEqual(sum(len(r) for r in results), 1)

    def test_task_duplicate_done_and_cancel(self):
        a = self.s.handle("add task buy milk", 1)
        b = self.s.handle("add task buy milk", 2)
        self.assertEqual(b["code"], "duplicate")
        identifier = a["data"]["id"]
        self.assertTrue(self.s.handle("complete task " + identifier, 3)["ok"])
        self.assertFalse(self.s.handle("cancel task " + identifier, 4)["ok"])
        self.assertEqual(self.p.list()["data"]["items"], [])

    def test_reminder_cancelled_before_due(self):
        a = self.s.handle("set a timer for 2 minutes", 1)
        self.assertTrue(self.s.handle("cancel timer " + a["data"]["id"], 2)["ok"])
        self.now[0] += 200
        self.assertEqual(self.p.claim_due(), [])

    def test_exact_date_and_invalid_inputs(self):
        self.assertTrue(
            self.s.handle("remind me at 2026-10-02T09:00 in America/Los_Angeles to call dad", 1)[
                "ok"
            ]
        )
        for text in [
            "remind me tomorrow to call dad",
            "set timer for 0 minutes",
            "remind me in 100000000 days to call dad",
            "cancel reminder random",
            "remind me at 2026-03-08T02:30 in America/Los_Angeles to call dad",
        ]:
            self.assertFalse(self.s.handle(text, 2)["ok"], text)

    def test_event_preview_confirm_duplicate_and_unrelated_yes(self):
        command = 'schedule event "Dentist" from 2026-10-02T09:00 to 2026-10-02T10:00 in America/Los_Angeles'
        self.assertEqual(self.s.handle(command, 1)["code"], "preview")
        self.assertEqual(self.p.list("event")["data"]["items"], [])
        self.assertTrue(self.s.handle("yes", 2)["ok"])
        self.s.handle(command, 3)
        self.assertEqual(self.s.handle("yes", 4)["code"], "duplicate")
        self.s.handle(command, 5)
        self.s.handle("Tell me a joke", 6)
        self.assertIsNone(self.s.handle("yes", 7))
        self.assertEqual(len(self.p.list("event")["data"]["items"]), 1)

    def test_event_expiry(self):
        self.s.handle('schedule event "Dentist" from 2026-10-02T09:00 to 2026-10-02T10:00', 1)
        self.now[0] += 901
        self.assertFalse(self.s.handle("yes", 2)["ok"])

    def test_named_completion_and_ambiguous_cancellation(self):
        self.s.handle("add task buy milk", 1)
        self.assertTrue(self.s.handle("complete task buy milk", 2)["ok"])
        self.p.add("reminder", "water", self.now[0] + 60)
        self.p.add("reminder", "water", self.now[0] + 120)
        self.assertEqual(self.s.handle("cancel reminder water", 3)["code"], "ambiguous")

    def test_natural_clock_time_requires_am_pm(self):
        self.assertTrue(self.s.handle("remind me tomorrow at 9 am to call dad", 1)["ok"])
        self.assertFalse(self.s.handle("remind me tomorrow at 9 to call dad", 2)["ok"])

    def test_storage_failure_is_returned_by_dispatcher(self):
        from julius.brain import tools
        from julius.brain.dispatcher import Dispatcher

        self.p.path.write_bytes(b"bad database")
        d = Dispatcher(tools.REGISTRY, tools.SCHEMAS)
        out = d.human_utilities(self.s, "show my tasks", 1)
        self.assertEqual(out["code"], "storage_error")
        self.assertFalse(out["ok"])

    def test_calendar_overlaps_are_disclosed(self):
        self.p.add("event", "First", self.now[0] + 60, self.now[0] + 3600)
        out = self.p.add("event", "Second", self.now[0] + 120, self.now[0] + 1800)
        self.assertEqual(out["data"]["overlaps"], 1)

    def test_task_reference_cannot_cancel_another_kind(self):
        task = self.p.add("task", "milk")
        self.assertFalse(self.s.handle("cancel reminder " + task["data"]["id"], 1)["ok"])
        self.assertEqual(len(self.p.list("task")["data"]["items"]), 1)

    def test_transaction_failure_rolls_back(self):
        self.p.list()
        with self.assertRaises(RuntimeError):
            with self.p.db() as con:
                con.execute(
                    "INSERT INTO items(id,kind,title,created) VALUES('test','task','half write',?)",
                    (self.now[0],),
                )
                raise RuntimeError("power interruption simulation")
        self.assertEqual(self.p.list()["data"]["items"], [])

    def test_concurrent_duplicate_task_creation(self):
        self.p.list()

        def add(_):
            return Planner(self.tmp.name, clock=lambda: self.now[0]).add("task", "one task")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(add, range(8)))
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(sum(r["code"] == "created" for r in results), 1)
        self.assertEqual(len(self.p.list()["data"]["items"]), 1)

    def test_capacity_limit_does_not_drop_old_items(self):
        self.p.LIMIT = 2
        self.p.add("task", "one")
        self.p.add("task", "two")
        self.assertEqual(self.p.add("task", "three")["code"], "full")
        self.assertEqual(len(self.p.list()["data"]["items"]), 2)

    def test_corrupt_database_is_not_overwritten(self):
        self.p.path.write_bytes(b"bad database")
        with self.assertRaises(Exception):
            self.p.list()
        self.assertEqual(self.p.path.read_bytes(), b"bad database")


class ConversionTests(unittest.TestCase):
    def test_known_definitions(self):
        self.assertEqual(convert_units(1, "mi", "m"), "1 mi = 1609.344 m")
        self.assertEqual(convert_units(32, "f", "c"), "32 f = 0 c")
        self.assertEqual(convert_units(0, "c", "f"), "0 c = 32 f")
        self.assertEqual(convert_units(1, "lb", "kg"), "1 lb = 0.45359237 kg")

    def test_dimensions_ambiguous_volume_and_nonfinite_rejected(self):
        for args in [
            (1, "mi", "kg"),
            (1, "gallon", "l"),
            ("NaN", "m", "ft"),
            (-1, "k", "c"),
            (1, "usd", "eur"),
        ]:
            with self.assertRaises(ValueError):
                convert_units(*args)

    def test_dst_days_vs_hours(self):
        start = "2026-03-07T12:00"
        self.assertEqual(date_calculate(start, "add_days", amount=1), "2026-03-08T12:00:00-07:00")
        self.assertEqual(date_calculate(start, "add_hours", amount=24), "2026-03-08T13:00:00-07:00")
        self.assertEqual(
            date_calculate(start, "difference", end="2026-03-08T12:00"), "23 elapsed hours"
        )

    def test_ambiguous_nonexistent_and_invalid_zone_rejected(self):
        for stamp in ["2026-03-08T02:30", "2026-11-01T01:30"]:
            with self.assertRaises(ValueError):
                local_datetime(stamp, "America/Los_Angeles")
        self.assertEqual(
            local_datetime("2026-11-01T01:30-07:00", "America/Los_Angeles")
            .utcoffset()
            .total_seconds(),
            -25200,
        )

    def test_zone_conversion_leap_and_invalid_amount(self):
        self.assertEqual(
            date_calculate("2028-02-28T12:00", "add_days", amount=1), "2028-02-29T12:00:00-08:00"
        )
        self.assertEqual(
            date_calculate("2026-10-01T12:00-07:00", "convert_timezone", target_timezone="UTC"),
            "2026-10-01T19:00:00+00:00",
        )
        with self.assertRaises(ValueError):
            date_calculate("2026-10-01", "add_days", amount=0.5)


if __name__ == "__main__":
    unittest.main()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        from julius.agents.file_agent import FileAgent

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "Documents").mkdir()
        self.agent = FileAgent(self.root, self.root / "state")

    def read(self, name, query=None):
        from julius.brain.documents import read_document

        return read_document(name, query, self.agent)

    def write(self, name, data):
        p = self.root / "Documents" / name
        p.write_bytes(data)
        return p

    def test_text_citations_literal_search_and_scope(self):
        self.write("notes.txt", b"First line\nLaunch date is Friday\n")
        out = self.read("notes.txt", "Friday")
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["matches"][0]["location"], "line 2")
        self.assertFalse(self.read(str(self.root / "private.txt"))["ok"])
        outside = self.root / "private.txt"
        outside.write_text("private")
        (self.root / "Documents/link.txt").symlink_to(outside)
        self.assertFalse(self.read("link.txt")["ok"])

    def test_docx_body_and_entity_rejection(self):
        import io
        import zipfile

        def pack(xml):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as z:
                z.writestr("word/document.xml", xml)
            return data.getvalue()

        xml = b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Meeting Friday</w:t></w:r></w:p></w:body></w:document>'
        self.write("test.docx", pack(xml))
        out = self.read("test.docx")
        self.assertTrue(out["ok"])
        self.assertIn("paragraph 1", out["message"])
        self.write("bad.docx", pack(b"<!DOCTYPE foo>" + xml))
        self.assertFalse(self.read("bad.docx")["ok"])

    def test_blank_encrypted_and_malformed_pdf(self):
        import subprocess

        from julius.brain.documents import document_python

        script = 'from pypdf import PdfWriter; import sys; w=PdfWriter(); w.add_blank_page(100,100); w.encrypt("secret") if sys.argv[2]=="yes" else None; w.write(sys.argv[1])'
        for name, encrypted in [("blank.pdf", "no"), ("locked.pdf", "yes")]:
            p = self.root / "Documents" / name
            subprocess.run([document_python(), "-c", script, str(p), encrypted], check=True)
            out = self.read(name)
            self.assertFalse(out["ok"])
        self.write("bad.pdf", b"not a PDF")
        self.assertFalse(self.read("bad.pdf")["ok"])

    def test_pdf_real_text_and_page_citation(self):
        import subprocess

        from julius.brain.documents import document_python

        p = self.root / "Documents" / "sample.pdf"
        script = 'from reportlab.pdfgen import canvas; import sys; c=canvas.Canvas(sys.argv[1]); c.drawString(30,700,"Meeting Friday"); c.save()'
        subprocess.run([document_python(), "-c", script, str(p)], check=True)
        out = self.read("sample.pdf", "Friday")
        self.assertTrue(out["ok"])
        self.assertEqual(out["data"]["matches"][0]["location"], "page 1")

    def test_partial_extraction_and_unsupported_format(self):
        self.write("long.txt", b"Hello\n" * 250)
        out = self.read("long.txt")
        self.assertTrue(out["data"]["truncated"])
        self.write("old.doc", b"legacy format")
        self.assertFalse(self.read("old.doc")["ok"])

    def test_worker_timeout_is_stopped_and_reported(self):
        import subprocess
        from unittest.mock import patch

        self.write("notes.txt", b"Hello")
        with patch(
            "julius.brain.documents.subprocess.run",
            side_effect=subprocess.TimeoutExpired("parser", 12),
        ):
            self.assertEqual(self.read("notes.txt")["code"], "timeout")


class CandidateUtilityIntegrationTests(unittest.TestCase):
    setUp = PlanningTests.setUp

    def test_planning_and_conversions_are_handled_without_the_model(self):
        from unittest.mock import patch

        import julius.agents.files as jf
        import julius.brain.tools as jt
        from julius.agents.file_agent import FileAgent
        from julius.agents.file_session import FileSession
        from julius.brain.dispatcher import Dispatcher
        from julius.runtime import brain_turn
        from tests.harness import ProcessHarness, patched

        root = Path(self.tmp.name)
        (root / "Documents").mkdir()
        file_agent = FileAgent(root, root / "file_state")
        dispatcher = Dispatcher(jt.REGISTRY, jt.SCHEMAS, file_agent)
        h = ProcessHarness(
            validation_output=str(root / "validation.json"),
            utility_session=self.s,
            file_session=FileSession(file_agent),
        )
        for name in ("turn.handle_files", "turn.jt", "turn.jf"):
            del h.mocks[name]  # the real controllers run
        model = Mock(side_effect=AssertionError("model not needed"))

        def ask(
            session, *args, **kwargs
        ):  # the real brain turn, with a model that must stay unused
            with patched(**{"brain_turn.llm_chat": model}):
                return brain_turn.ask_streaming(session, *args, **kwargs)

        h.mocks["turn.ask_streaming"] = ask
        (root / "Documents" / "notes.txt").write_text("Launch Friday")
        commands = [
            "add task buy milk",
            "complete task buy milk",
            "remind me in 5 minutes to water plants",
            "convert 1 mi to m",
            'read document "notes.txt"',
        ]
        with (
            patch.object(jt, "_dispatcher", dispatcher),
            patch.object(jf, "agent", return_value=file_agent),
        ):
            for command in commands + ["show my tasks"] * 10:
                h.process(command)
            h["turn.speak"].side_effect = RuntimeError("playback offline")
            h.process("add task saved despite speaker failure")
        turns = h.session.validation_turns
        self.assertTrue(all(r["error"] is None for r in turns[:-1]))
        self.assertIn("playback offline", turns[-1]["error"])
        self.assertLessEqual(len(h.session.history), 9)
        self.assertIn(
            "saved despite speaker failure",
            [r["title"] for r in self.p.list("task")["data"]["items"]],
        )
        self.assertIn("1609.344", turns[3]["answer"])
        self.assertIn("Launch Friday", turns[4]["answer"])
        self.assertEqual(turns[4]["dispatcher_tasks"][0]["agent"], "documents")
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
