"""Conversation summaries and recaps name the user from [profile], never a hard-coded person."""

import unittest
from unittest import mock

import julius.conversations.manager as manager
from julius import profile

ROWS = [{"role": "user", "text": "hello"}, {"role": "assistant", "text": "hi"}]


class ConversationProfile(unittest.TestCase):
    def test_no_name_set_says_the_user(self):
        with mock.patch.object(profile, "_profile", return_value={}):
            self.assertTrue(manager._transcript(ROWS).startswith("User: hello\n"))
            self.assertIn(
                "between the user and their", manager._with_profile(manager.SUMMARY_SYSTEM)
            )
            self.assertIn("addressed to the user", manager._with_profile(manager.RECAP_SYSTEM))

    def test_a_profile_name_is_used(self):
        with mock.patch.object(profile, "_profile", return_value={"name": "Sam"}):
            self.assertTrue(manager._transcript(ROWS).startswith("Sam: hello\n"))
            self.assertIn("between Sam and their", manager._with_profile(manager.SUMMARY_SYSTEM))
            self.assertIn("addressed to Sam", manager._with_profile(manager.RECAP_SYSTEM))

    def test_no_person_is_hard_coded(self):
        for text in (manager.SUMMARY_SYSTEM, manager.RECAP_SYSTEM):
            self.assertNotIn("Roy", text)
            self.assertNotIn(" his ", text)


if __name__ == "__main__":
    unittest.main()
