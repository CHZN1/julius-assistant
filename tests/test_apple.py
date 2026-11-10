"""Unit tests for apple."""

import json
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from julius.agents.utility_session import UtilitySession
from julius.brain.apple import AppleProvider
from julius.brain.planning import Planner


class AppleTests(unittest.TestCase):
    def test_explicit_completion_preserves_reminder_and_syncs(self):
        s = UtilitySession(self.p, provider=self.a)
        created = s.handle("remind me in 5 minutes to water", 1)
        self.run.reset_mock()
        self.assertIsNone(s.handle("yes", 2))
        self.run.assert_not_called()
        self.assertTrue(s.handle("complete reminder water", 3)["ok"])
        request = json.loads(self.run.call_args.kwargs["input"])
        self.assertEqual(request["action"], "complete")
        self.assertEqual(request["remote_id"], "owned")
        with self.p.db() as con:
            self.assertEqual(
                con.execute(
                    "SELECT status FROM items WHERE id=?", (created["data"]["id"],)
                ).fetchone()["status"],
                "done",
            )

    def test_failed_completion_keeps_local_reminder(self):
        s = UtilitySession(self.p, provider=self.a)
        s.handle("remind me in 5 minutes to water", 1)
        self.run.return_value.stdout = json.dumps(
            {"ok": False, "code": "changed", "message": "Ownership changed."}
        )
        self.assertFalse(s.handle("complete reminder water", 2)["ok"])
        self.assertEqual(len(self.p.list("reminder")["data"]["items"]), 1)

    def test_ambiguous_completion_does_not_touch_apple(self):
        s = UtilitySession(self.p, provider=self.a)
        s.handle("remind me in 5 minutes to water", 1)
        s.handle("remind me in 6 minutes to water", 2)
        self.run.reset_mock()
        self.assertEqual(s.handle("complete reminder water", 3)["code"], "ambiguous")
        self.run.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Planner(self.tmp.name, clock=lambda: 1800000000)
        self.run = Mock(
            return_value=Mock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "code": "created",
                        "remote_id": "owned",
                        "message": "Saved to Apple.",
                    }
                ),
            )
        )
        self.a = AppleProvider(self.p, {"event": "calendar", "reminder": "list"}, self.run)

    def test_sync_journal_duplicate_and_safe_data_transport(self):
        self.p.add("reminder", "test", 1800000010)
        out = self.a.sync("local", "reminder", "$(touch bad)", 1800000010)
        self.assertTrue(out["ok"])
        self.assertTrue(self.a.sync("local", "reminder", "test", 1800000010)["ok"])
        self.assertEqual(self.run.call_count, 1)
        args, kwargs = self.run.call_args
        self.assertNotIn("$(touch bad)", args[0])
        self.assertIn("$(touch bad)", kwargs["input"])

    def test_timeout_retry_preserves_token(self):
        self.run.side_effect = subprocess.TimeoutExpired("Apple", 45)
        self.assertFalse(self.a.sync("local", "event", "test", 1800000010, 1800000100)["ok"])
        first = json.loads(self.run.call_args.kwargs["input"])["token"]
        self.run.side_effect = None
        self.assertTrue(self.a.sync("local", "event", "test", 1800000010, 1800000100)["ok"])
        self.assertEqual(first, json.loads(self.run.call_args.kwargs["input"])["token"])

    def test_unresolved_creation_cannot_claim_cancelled(self):
        self.run.side_effect = subprocess.TimeoutExpired("Apple", 45)
        self.a.sync("local", "event", "test", 1800000010, 1800000100)
        self.assertFalse(self.a.cancel("local")["ok"])

    def test_confirmed_event_routes_to_apple_and_reminder_errors_stay_truthful(self):
        s = UtilitySession(self.p, provider=self.a)
        preview = s.handle('schedule event "Test" from 2027-10-05T09:00 to 2027-10-05T10:00', 1)
        self.assertEqual(preview["code"], "preview")
        self.run.assert_not_called()
        self.assertTrue(s.handle("yes", 2)["ok"])
        self.assertEqual(self.run.call_count, 1)
        self.run.return_value = Mock(
            returncode=0,
            stdout=json.dumps({"ok": False, "code": "denied", "message": "Permission denied."}),
        )
        out = s.handle("remind me in 5 minutes to test", 3)
        self.assertEqual(out["code"], "partial_sync")
        self.assertIn("not confirmed", out["message"])
        self.assertIn("not confirmed", out["data"]["speech_message"])

    def test_failed_external_cancel_does_not_cancel_local(self):
        s = UtilitySession(self.p, provider=self.a)
        s.handle("remind me in 5 minutes to water", 1)
        self.run.return_value = Mock(
            returncode=0,
            stdout=json.dumps({"ok": False, "code": "changed", "message": "Apple item changed."}),
        )
        self.assertFalse(s.handle("cancel reminder water", 2)["ok"])
        self.assertEqual(len(self.p.list("reminder")["data"]["items"]), 1)

    def test_unconfigured_provider_does_not_invent_connection(self):
        a = AppleProvider(self.p, {}, self.run)
        s = UtilitySession(self.p, provider=a)
        self.assertFalse(a.enabled)
        self.assertEqual(s.handle("remind me in 1 minute to water", 1)["code"], "created")
        self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
