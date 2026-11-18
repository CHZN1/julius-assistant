"""A plain noun names the files ("move the invoices" -> *invoice*): the move is still previewed and needs a yes."""

import unittest
from pathlib import Path

from julius.agents import file_router as fr


class NounSelectionTests(unittest.TestCase):
    def test_nouns(self):
        self.assertEqual(fr._file_pattern("the invoices"), "*invoice*")
        self.assertEqual(fr._file_pattern("my receipts"), "*receipt*")

    def test_generic_words_still_ask(self):
        for words in ("the files", "the stuff", "the things"):
            self.assertIn("clarification", fr._file_pattern(words), words)

    def test_the_whole_command(self):
        got = fr.parse("Move the invoices from Downloads into Documents/Invoices", Path.home())
        self.assertEqual(
            (got["action"], got["pattern"], got["source"], got["destination"]),
            ("move", "*invoice*", "Downloads", "Documents/Invoices"),
        )


if __name__ == "__main__":
    unittest.main()
