"""Spoken replies never describe tool calls: the filter, the stream, and the eval check."""

import unittest

from julius.runtime.text import is_narration, strip_narration


class Narration(unittest.TestCase):
    def test_tool_call_narration_is_caught(self):
        for t in (
            "I need to actually run that edit, so I'll call the sprite tool now.",
            "Let me call the weather tool.",
            "I'm using the search tool to check.",
            "I'll invoke it now.",
            "I used sprite_edit to change it.",
            "Calling image_style(source='dropped').",
        ):
            self.assertTrue(is_narration(t), t)

    def test_answers_are_kept(self):
        for t in (
            "Done: a bigger beard.",
            "It is 18 degrees in Paris.",
            "Use the tool on the left to crop it.",
            "I'll run a search for you.",
            "The function of the liver is filtering blood.",
        ):
            self.assertFalse(is_narration(t), t)

    def test_strip_keeps_the_answer(self):
        self.assertEqual(
            strip_narration(
                "I need to actually run that edit, so I'll call the sprite tool now. Done: sunglasses and a cap."
            ),
            "Done: sunglasses and a cap.",
        )

    def test_the_routing_eval_fails_a_narrated_reply(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
        import routing_eval

        case = {"id": "x", "text": "add glasses", "expect": {"path": "tool", "tool": "sprite_edit"}}
        good = {"path": "tool", "tool": "sprite_edit", "final": "Done: square glasses."}
        bad = {
            "path": "tool",
            "tool": "sprite_edit",
            "final": "I'll call the sprite tool now. Done: square glasses.",
        }
        self.assertTrue(all(routing_eval.judge(case, good)))
        self.assertFalse(all(routing_eval.judge(case, bad)))


if __name__ == "__main__":
    unittest.main()
