"""Side-panel stage 4: the research emitter, 'open source N', 'save this card', 'close the panel' and their config."""

import io
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

import julius.agents.research as research
import julius.config as config
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panel_emit as pe
import julius.ui_bridge.panelfiles as pf


def png(color=(10, 120, 200)):
    b = io.BytesIO()
    Image.new("RGB", (60, 40), color).save(b, "PNG")
    return b.getvalue()


RESULT = {
    "question": "How tall is the tallest building in the world?",
    "summary": "The Burj Khalifa is 828 m tall.",
    "findings": [
        {"claim": "It is 828 m (2,717 ft).", "url": "https://www.example.org/tower"},
        {"claim": "It opened in 2010.", "url": "https://other.example.com/a"},
    ],
    "dropped_findings": [],
    "sources": [
        {"url": "https://www.example.org/tower", "title": "The Tower"},
        {"url": "https://other.example.com/a", "title": "History"},
    ],
    "images": [
        {
            "url": "https://www.example.org/i1.png",
            "alt": "dusk",
            "page": "https://www.example.org/tower",
        },
        {
            "url": "https://www.example.org/bad.png",
            "alt": "bad",
            "page": "https://www.example.org/tower",
        },
    ],
    "stopped": "done",
}
OTHER = dict(
    RESULT,
    question="Who painted the ceiling of the Sistine Chapel?",
    summary="Michelangelo.",
    sources=[{"url": "https://art.example.net/m", "title": "Michelangelo"}],
    findings=[{"claim": "Michelangelo painted it.", "url": "https://art.example.net/m"}],
    images=[],
)


class FakeFetcher:
    def fetch_image(self, url, max_bytes=5_000_000):
        if "bad" in url:
            raise OSError("blocked")
        return 200, "image/png", png()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.hub = ev.EventHub()
        self.sent = []
        self.hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.root / "panel")
        self.panel = pn.PanelHub(self.hub, base="http://127.0.0.1:1/tok")

    def tearDown(self):
        self.tmp.cleanup()


class Emitter(Base):
    def test_card_goes_out_at_once_and_images_arrive_as_an_update(self):
        sink = pe.make_sink(self.panel, self.files, lambda: 7, FakeFetcher)
        sink(RESULT)
        self.assertEqual(self.sent[0]["type"], "panel.research")
        self.assertEqual(self.sent[0]["turn"], 7)
        self.assertEqual(self.sent[0]["images"], [])
        for _ in range(100):
            if any(m["type"] == "panel.update" for m in self.sent):
                break
            time.sleep(0.05)
        up = [m for m in self.sent if m["type"] == "panel.update"][0]
        self.assertEqual(len(up["images"]), 1)  # the one that failed was dropped, not fatal
        self.assertTrue(self.files.local_path(up["images"][0]["image"]).exists())
        self.assertEqual(up["images"][0]["page_title"], "The Tower")

    def test_a_broken_result_never_raises(self):
        pe.make_sink(self.panel, self.files)({"nonsense": 1})
        pe.make_sink(None, None)(RESULT)

    def test_research_task_hands_the_result_to_the_sink_and_survives_a_failing_one(self):
        got = []
        research.set_panel_sink(lambda r: got.append(r))
        try:

            class Agent:
                def __init__(self, brain):
                    pass

                def run(self, q):
                    return dict(RESULT, id="x")

            class Brain:
                auth_bad = False

                class tracker:
                    cap_reached = staticmethod(lambda: False)

            orig = research.ResearchAgent
            research.ResearchAgent = Agent
            try:
                out = research.research_task({"question": "q"}, Brain())
                self.assertEqual(len(got), 1)
                self.assertIn("828", out)
                research.set_panel_sink(lambda r: 1 / 0)
                self.assertIn(
                    "828", research.research_task({"question": "q"}, Brain())
                )  # the answer survives
            finally:
                research.ResearchAgent = orig
        finally:
            research.set_panel_sink(None)

    def test_start_panel_off_or_port_taken(self):
        self.assertIsNone(pe.start_panel(self.hub, {"panel": False}, self.root / "p"))
        self.assertIsNone(pe.start_panel(None, {"panel": True}, self.root / "p"))
        a = pe.start_panel(self.hub, {"panel": True, "panel_port": 0}, self.root / "p1")
        try:
            self.assertIsNotNone(a)
            self.assertTrue(a[0].base.startswith("http://127.0.0.1:"))
            self.assertIsNone(
                pe.start_panel(
                    ev.EventHub(), {"panel": True, "panel_port": a[2].port}, self.root / "p2"
                )
            )  # taken: no panel, no crash
        finally:
            a[2].stop()


class Commands(Base):
    def setUp(self):
        super().setUp()
        self.opened = []
        self.panel.research(RESULT)
        self.first = self.panel.lookup()["panel_id"]
        self.panel.research(OTHER)  # the newest card

    def say(self, text, saved=None):
        return pe.handle(self.panel, self.files, text, saved, self.opened.append)

    def test_open_source_uses_the_stored_url_of_the_newest_card(self):
        self.assertEqual(self.say("open source 1"), "Opening source 1.")
        self.assertEqual(self.opened, ["https://art.example.net/m"])
        for form in (
            "Open source one.",
            "please open the source number 1",
            "open link #1",
            "Can you open source 1?",
        ):
            self.opened.clear()
            self.assertEqual(self.say(form), "Opening source 1.", form)
            self.assertEqual(self.opened, ["https://art.example.net/m"])
        self.assertIn(
            "has 1 source", self.say("open source 2")
        )  # out of range: told, nothing opened
        self.assertEqual(self.opened, ["https://art.example.net/m"])

    def test_open_source_from_an_older_card_by_its_question(self):
        self.opened.clear()
        self.assertEqual(
            self.say("open source 2 from the research on the tallest building"), "Opening source 2."
        )
        self.assertEqual(self.opened, ["https://other.example.com/a"])
        self.assertEqual(
            self.say("open source 1 from the research on tallest building in the world"),
            "Opening source 1.",
        )
        self.assertIn(
            "couldn't find", self.say("open source 1 from the research on quantum chromodynamics")
        )
        self.assertEqual(self.opened[-1], "https://www.example.org/tower")

    def test_the_url_never_comes_from_the_words(self):
        self.opened.clear()
        self.assertIsNone(
            self.say("open source 1 https://evil.example/ now")
        )  # not a command: goes on to the brain
        self.assertIsNone(self.say("open javascript:alert(1)"))
        self.assertEqual(self.opened, [])

    def test_unrelated_text_and_no_card(self):
        for t in (
            "what is the weather",
            "open the pod bay doors",
            "save the whales",
            "",
            "x" * 400,
        ):
            self.assertIsNone(self.say(t), t)
        empty = pn.PanelHub(ev.EventHub())
        self.assertIn("don't have a research card", pe.handle(empty, self.files, "open source 1"))
        self.assertIn("don't have a card", pe.handle(empty, self.files, "save this card"))

    def test_close_the_panel_publishes_a_clear(self):
        self.sent.clear()
        self.assertEqual(self.say("close the panel"), "Closed the panel.")
        self.assertEqual(self.sent[0]["type"], "panel.clear")
        self.assertEqual(self.sent[0]["scope"], "all")

    def test_save_this_card_writes_markdown_and_images_to_a_new_folder(self):
        self.panel.clear(reason="x")
        self.panel.research(RESULT)
        sink = pe.make_sink(self.panel, self.files, fetcher_factory=FakeFetcher)
        self.panel.clear(reason="x")
        sink(RESULT)
        for _ in range(100):
            if self.panel.lookup() and self.panel.lookup()["images"]:
                break
            time.sleep(0.05)
        dest = self.root / "Saved"
        said = self.say("save this card", str(dest))
        self.assertIn("Saved to Saved", said)
        folders = list(dest.iterdir())
        self.assertEqual(len(folders), 1)
        md = (folders[0] / "card.md").read_text()
        self.assertIn("# How tall is the tallest building in the world?", md)
        self.assertIn("The Burj Khalifa is 828 m tall.", md)
        self.assertIn("<https://www.example.org/tower>", md)
        self.assertIn("![dusk](images/1.", md)
        self.assertEqual(len(list((folders[0] / "images").iterdir())), 1)
        self.assertTrue(
            folders[0].name.endswith("how-tall-is-the-tallest-building-in-the")
        )  # a plain, safe name
        self.say("save this card", str(dest))
        self.assertEqual(len(list(dest.iterdir())), 2)  # a second save never overwrites the first

    def test_save_refuses_icloud_and_relative_folders(self):
        icloud = str(
            Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Julius Saved"
        )
        self.assertIn("iCloud", self.say("save this card", icloud))
        self.assertIn("full path", self.say("save this card", "Julius Saved"))
        self.assertEqual(
            pe.saved_dir(None), Path("~/Julius Saved").expanduser()
        )  # the default is local, in the home folder

    def test_names_cannot_escape_the_folder(self):
        self.panel.clear(reason="x")
        self.panel.research(dict(OTHER, question="../../etc/passwd <script>"))
        dest = self.root / "Saved"
        self.say("save this card", str(dest))
        (f,) = list(dest.iterdir())
        self.assertEqual(f.parent, dest)
        self.assertRegex(f.name, r"^\d{4}-\d\d-\d\d \d{4} [a-z0-9-]+$")


class Settings(unittest.TestCase):
    def test_defaults_and_validation(self):
        ui = config.load_config()["ui"]
        self.assertTrue(ui["panel"])
        self.assertEqual(ui["panel_port"], 8785)
        self.assertEqual(ui["saved_dir"], "~/Julius Saved")


if __name__ == "__main__":
    unittest.main()
