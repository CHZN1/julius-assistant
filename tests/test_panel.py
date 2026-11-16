"""Unit tests for the side-panel events (schema conformance, sanitising, replay through hello)."""

import json
import re
import unittest
from pathlib import Path

import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn

DOC = Path(__file__).resolve().parent.parent / "docs" / "EVENT_FEED.md"

RESULT = {
    "question": "How tall is the tower?",
    "summary": "It is 830 m tall.",
    "findings": [
        {"claim": "The tower is 830 m tall.", "url": "https://www.example.org/tower"},
        {"claim": "It opened in 2010.", "url": "https://other.example.com/a"},
    ],
    "dropped_findings": [{"claim": "x"}],
    "sources": [
        {"url": "https://www.example.org/tower", "title": "The Tower"},
        {"url": "https://other.example.com/a", "title": "History"},
        {"url": "javascript:alert(1)", "title": "bad"},
    ],
    "stopped": "done",
}
IMG = {
    "image": "/p/abcdefgh12.png",
    "alt": "photo",
    "page_url": "https://www.example.org/tower",
    "page_title": "The Tower",
}


class Collect:
    def __init__(self):
        self.sent = []

    def offer(self, data):
        self.sent.append(data)


class Cleaning(unittest.TestCase):
    def test_strips_control_bidi_and_caps(self):
        s = pn.clean("a\x00b‮c\n\td" + "x" * 500, 20)
        self.assertEqual(len(s), 20)
        self.assertNotIn("\x00", s)
        self.assertNotIn("‮", s)
        self.assertTrue(s.startswith("abc d"))

    def test_clean_text_keeps_lines_but_nothing_else(self):
        s = pn.clean_text("**a**\x00\n\n\n\n- one\r\n- two\u202e\t x", 100)
        self.assertEqual(s, "**a**\n\n- one\n- two x")
        self.assertEqual(len(pn.clean_text("x\n" * 500, 20)), 20)
        self.assertEqual(pn.clean_text(None, 10), "")

    def test_urls(self):
        self.assertTrue(pn.clean_url("https://example.org/a?b=1"))
        for bad in (
            "javascript:alert(1)",
            "data:text/html,x",
            "file:///etc/passwd",
            "https://u:p@example.org/",
            "https://a b/",
            "//x.org",
            "",
        ):
            self.assertEqual(pn.clean_url(bad), "", bad)


class Cards(unittest.TestCase):
    def setUp(self):
        self.hub = ev.EventHub(clock=lambda: 1.0)
        self.panel = pn.PanelHub(self.hub, base="http://127.0.0.1:8785/tok", clock=lambda: 1.0)
        self.c = Collect()
        self.hub.add(self.c)

    def msgs(self):
        return [self._decode(b) for b in self.c.sent]

    @staticmethod
    def _decode(b):
        n = b[1] & 0x7F
        off = 2
        if n == 126:
            n = int.from_bytes(b[2:4], "big")
            off = 4
        elif n == 127:
            n = int.from_bytes(b[2:10], "big")
            off = 10
        return json.loads(b[off : off + n])

    def test_research_conforms_and_numbers_citations(self):
        pid = self.panel.research(RESULT, [IMG], turn=3)
        (m,) = self.msgs()
        self.assertEqual(pn.validate(m), [])
        self.assertEqual(m["panel_id"], pid)
        self.assertEqual([s["n"] for s in m["sources"]], [1, 2])  # javascript: source dropped
        self.assertEqual(m["sources"][0]["site"], "example.org")
        self.assertEqual([f["cite"] for f in m["findings"]], [[1], [2]])
        self.assertEqual(m["dropped"], 1)

    def test_hostile_fields_are_cleaned(self):
        r = dict(RESULT, summary="<img src=x onerror=alert(1)>\x07‮" + "y" * 5000)
        r["sources"] = [{"url": "https://e.org/", "title": "<script>x</script>" + "t" * 999}]
        self.panel.research(r)
        (m,) = self.msgs()
        self.assertEqual(pn.validate(m), [])
        self.assertLessEqual(len(m["summary"]), pn.CAPS["summary"])
        self.assertLessEqual(len(m["sources"][0]["title"]), pn.CAPS["title"])
        self.assertNotIn("‮", m["summary"])

    def test_summary_and_claims_keep_markdown_lines(self):
        r = dict(RESULT, summary="Intro **bold**\n\n- a\n- b")
        self.panel.research(r)
        self.assertEqual(self.msgs()[0]["summary"], "Intro **bold**\n\n- a\n- b")
        self.assertNotIn("\n", self.msgs()[0]["sources"][0]["title"])

    def test_remote_image_refs_rejected(self):
        bad = dict(IMG, image="https://evil.example/x.png")
        self.panel.research(RESULT, [bad, dict(IMG, image="/p/../../etc.png")])
        self.assertEqual(self.msgs()[0]["images"], [])

    def test_oversize_is_trimmed_under_cap(self):
        r = dict(
            RESULT, findings=[{"claim": "c" * 600, "url": "https://www.example.org/tower"}] * 12
        )
        r["sources"] = [
            {"url": f"https://e{i}.org/" + "p" * 1900, "title": "t" * 200} for i in range(12)
        ]
        self.panel.research(r, [IMG] * 8)
        raw = json.dumps(self.msgs()[0], separators=(",", ":"))
        self.assertLessEqual(len(raw.encode()), pn.MAX_BYTES)

    def test_update_folds_into_hello(self):
        pid = self.panel.research(RESULT)
        self.assertTrue(self.panel.update(pid, images=[IMG]))
        self.assertFalse(self.panel.update("nope", images=[IMG]))
        upd = self.msgs()[1]
        self.assertEqual(pn.validate(upd), [])
        hello = self.hub.hello()
        self.assertEqual(hello["panel"]["base"], "http://127.0.0.1:8785/tok")
        self.assertEqual(len(hello["panel"]["cards"][0]["images"]), 1)

    def test_history_is_last_twenty_and_clear(self):
        ids = [self.panel.research(RESULT) for _ in range(22)]
        cards = self.hub.hello()["panel"]["cards"]
        self.assertEqual([c["panel_id"] for c in cards], ids[2:])
        self.panel.clear(ids[-1], "dismissed")
        self.assertEqual(len(self.hub.hello()["panel"]["cards"]), 19)
        self.panel.clear(reason="new_topic")
        self.assertEqual(self.hub.hello()["panel"]["cards"], [])
        self.assertEqual(pn.validate(self.msgs()[-1]), [])

    def test_hello_without_panel_is_unchanged(self):
        self.assertNotIn("panel", ev.EventHub().hello())

    def test_never_raises_on_garbage(self):
        self.assertIsNone(self.panel.research({"findings": "nope", "sources": None}))
        self.assertEqual(self.c.sent, [])


class Docs(unittest.TestCase):
    def test_doc_example_conforms(self):
        blocks = re.findall(r"```json\n(.*?)\n```", DOC.read_text(), re.S)
        panel_blocks = [json.loads(b) for b in blocks if '"panel.' in b]
        self.assertTrue(panel_blocks)
        for m in panel_blocks:
            self.assertEqual(pn.validate(m), [], m)

    def test_doc_lists_every_type(self):
        text = DOC.read_text()
        for t in pn.SCHEMAS:
            self.assertIn(f"`{t}`", text)

    def test_old_client_ignores_unknown(self):
        m = json.loads(json.dumps({"type": "panel.future", "x": 1}))
        self.assertEqual(pn.validate(m), ["unknown panel type"])


if __name__ == "__main__":
    unittest.main()
