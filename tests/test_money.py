"""Spoken spending limits parse exactly; a number with no unit after a limit phrase is ambiguous and asks."""

import unittest

from julius.money import find_cap, parse_amount


class Amounts(unittest.TestCase):
    def test_exact_amounts(self):
        cases = {
            "50 cents": 0.50,
            "fifty cents": 0.50,
            "1 cent": 0.01,
            "150 cents": 1.50,
            "50¢": 0.50,
            "$0.50": 0.50,
            "$.50": 0.50,
            "$ 3": 3.0,
            "$2.25 dollars": 2.25,
            "half a dollar": 0.50,
            "half a buck": 0.50,
            "a dollar fifty": 1.50,
            "one dollar fifty": 1.50,
            "a dollar and fifty cents": 1.50,
            "two dollars twenty-five": 2.25,
            "2 dollars and 5 cents": 2.05,
            "2 bucks": 2.0,
            "a buck": 1.0,
            "two and a half dollars": 2.50,
            "1.5 dollars": 1.50,
            "three dollars": 3.0,
            "twenty dollars": 20.0,
            "0.75": 0.75,
            "1.50": 1.50,
            "3 usd": 3.0,
        }
        for text, want in cases.items():
            a = parse_amount(text)
            self.assertIsNotNone(a, text)
            self.assertFalse(a.ambiguous, text)
            self.assertAlmostEqual(a.value, want, places=6, msg=text)
            self.assertEqual(a.end, len(text), text)

    def test_a_bare_whole_number_is_ambiguous(self):
        for text in ("50", "fifty", "3"):
            a = parse_amount(text)
            self.assertTrue(a.ambiguous, text)
            self.assertIsNone(a.value, text)

    def test_not_money(self):
        for text in ("", "some", "the tests", "dollars"):
            self.assertIsNone(parse_amount(text), text)


class Caps(unittest.TestCase):
    def cap(self, text):
        c = find_cap(text)
        return None if c is None else (c.value, c.ambiguous)

    def test_limit_phrases(self):
        cases = {
            "fix calc.py in demo and limit it to 50 cents": 0.50,
            "fix calc.py in demo, limit it to $0.50": 0.50,
            "fix it with half a dollar": 0.50,
            "fix it and spend a dollar fifty": 1.50,
            "fix it for 2 bucks in demo": 2.0,
            "fix it with a 3 dollar limit": 3.0,
            "fix it with 50 dollars": 50.0,
            "budget of 1.25": 1.25,
            "capped at two dollars": 2.0,
            "spend at most 75 cents": 0.75,
            "no more than a buck": 1.0,
            "up to 4 dollars": 4.0,
        }
        for text, want in cases.items():
            got = self.cap(text)
            self.assertIsNotNone(got, text)
            self.assertAlmostEqual(got[0], want, places=6, msg=text)
            self.assertFalse(got[1], text)

    def test_the_span_covers_the_whole_phrase(self):
        text = "fix calc.py and limit it to 50 cents in julius-runas-demo"
        c = find_cap(text)
        self.assertEqual(
            " ".join((text[: c.start] + text[c.end :]).split()),
            "fix calc.py and in julius-runas-demo",
        )
        text = "fix it with a 3 dollar limit"
        c = find_cap(text)
        self.assertEqual(text[: c.start].strip(), "fix it")
        self.assertEqual(c.end, len(text))

    def test_a_limit_with_no_unit_asks(self):
        for text in ("limit it to 50", "fix it, budget of fifty", "cap it at 3 in demo"):
            got = self.cap(text)
            self.assertEqual(got, (None, True), text)

    def test_numbers_that_are_not_money_are_left_alone(self):
        for text in (
            "add a test with 2 cases",
            "fix it up to 3 times",
            "limit it to 2 files",
            "fix the bug for me",
            "rewrite it with fifty lines",
            "fix the 50 cent rounding bug",
        ):
            self.assertIsNone(find_cap(text), text)


if __name__ == "__main__":
    unittest.main()
