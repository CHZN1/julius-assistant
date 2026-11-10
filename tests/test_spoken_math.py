"""Unit tests for spoken math."""

import unittest
from unittest.mock import Mock

from julius.audio.spoken_math import arithmetic_expression
from julius.brain.calculator import evaluate
from julius.brain.intents import LiveIntentRouter


class SpokenMathTests(unittest.TestCase):
    def test_spoken_and_numeric_requests_use_exact_calculator(self):
        for text, expression, result in [
            ("What is two plus two?", "2+2", 4),
            ("What's twenty-one times four?", "21*4", 84),
            ("calculate negative three minus negative two", "-3--2", -1),
            ("ninety nine divided by three", "99/3", 33),
            ("17 times 4839", "17*4839", 82263),
            ("2.5 plus 3", "2.5+3", 5.5),
            ("-2-3", "-2-3", -5),
            ("two multiplied by eight", "2*8", 16),
        ]:
            self.assertEqual(
                LiveIntentRouter().resolve(text), ("calculate", {"expression": expression})
            )
            self.assertEqual(evaluate(expression), result)

    def test_explanations_units_and_ambiguous_expressions_are_not_actions(self):
        for text in [
            "Explain why two plus two is four",
            "two plus two houses",
            "two plus two plus three",
            'two plus __import__("os")',
            "one hundred plus two",
            "twenty zero plus one",
            "two point five plus one",
            "two plus half",
            "twenty-one",
            "negative two",
            "-2",
        ]:
            self.assertIsNone(arithmetic_expression(text), text)

    def test_actual_candidate_bypasses_model_for_number_words(self):
        from tests.harness import TurnHarness

        h = TurnHarness()
        h.llm_chat = Mock(side_effect=AssertionError("brain should not load"))
        answer = h.ask("What is two plus two?", "", speak_fn=lambda _: None)
        self.assertEqual(answer, "That's 4.")
        h.llm_chat.assert_not_called()

    def test_compact_numeric_spelling_still_routes(self):
        self.assertEqual(arithmetic_expression("2times3"), "2*3")


if __name__ == "__main__":
    unittest.main()
