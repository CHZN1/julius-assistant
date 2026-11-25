"""Side-panel stage 5a: render_chart (data in, picture out), table cards, image gallery cards, the tool wiring."""

import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import julius.brain.tools as tools
import julius.ui_bridge.charts as charts
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panel_emit as pe
import julius.ui_bridge.panelfiles as pf
from julius.brain.dispatcher import Dispatcher

BAR = {
    "kind": "bar",
    "title": "Tallest buildings",
    "unit": "metres",
    "labels": ["Burj Khalifa", "Merdeka 118", "Shanghai Tower"],
    "series": [{"name": "Height", "values": [828, 679, 632]}],
}
TABLE = {
    "kind": "table",
    "title": "Heights",
    "columns": ["Building", "Metres"],
    "rows": [["Burj Khalifa", "828"], ["Merdeka 118", 679]],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(Path(self.tmp.name) / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        charts.set_panel(self.panel, self.files, lambda: 9)

    def tearDown(self):
        charts.set_panel(None, None)
        self.tmp.cleanup()


class Spec(unittest.TestCase):
    def bad(self, **change):
        args = {**BAR, **change}
        with self.assertRaises(charts.ChartProblem):
            charts.clean_spec(args)

    def test_good_specs_are_cleaned_and_bounded(self):
        spec = charts.clean_spec({**BAR, "title": "T\x07itle‮"})
        self.assertEqual(spec["title"], "Title")
        self.assertEqual(spec["series"][0]["values"], [828.0, 679.0, 632.0])
        self.assertEqual(charts.clean_spec(TABLE)["rows"][1], ["Merdeka 118", "679"])

    def test_bad_specs_are_refused_with_a_speakable_reason(self):
        self.bad(kind="scatter")
        self.bad(labels=[])
        self.bad(labels=["a"] * 31, series=[{"name": "s", "values": [1] * 31}])
        self.bad(series=[{"name": "s", "values": [1, 2]}])  # one value per label
        self.bad(series=[{"name": "s", "values": [1, 2, float("nan")]}])
        self.bad(series=[{"name": "s", "values": [1, 2, True]}])
        self.bad(series=[{"name": "s", "values": [1, 2, "3"]}])
        self.bad(series=[{"name": str(i), "values": [1, 2, 3]} for i in range(6)])
        self.bad(kind="pie", series=[{"name": "s", "values": [1, -1, 1]}])
        self.bad(kind="pie", labels=["a"], series=[{"name": "s", "values": [1]}])
        with self.assertRaises(charts.ChartProblem):
            charts.clean_spec({**TABLE, "rows": [["only one cell"]]})
        with self.assertRaises(charts.ChartProblem):
            charts.clean_spec({**TABLE, "rows": [["a", "b"]] * 51})


class Drawing(Base):
    def test_every_kind_draws_a_transparent_png(self):
        for kind in ("bar", "hbar", "line"):
            png = charts.draw_png(charts.clean_spec({**BAR, "kind": kind}))
            im = Image.open(io.BytesIO(png))
            self.assertEqual((im.format, im.mode), ("PNG", "RGBA"), kind)
            self.assertEqual(im.getpixel((0, 0))[3], 0)  # transparent corner
        pie = {
            "kind": "pie",
            "title": "P",
            "labels": ["a", "b"],
            "series": [{"name": "s", "values": [1, 3]}],
        }
        self.assertGreater(len(charts.draw_png(charts.clean_spec(pie))), 1000)

    def test_multi_series_and_many_labels_still_draw(self):
        labels = [f"label number {i}" for i in range(30)]
        spec = {
            "kind": "line",
            "title": "x",
            "labels": labels,
            "series": [{"name": f"s{k}", "values": list(range(30))} for k in range(5)],
        }
        self.assertGreater(len(charts.draw_png(charts.clean_spec(spec))), 1000)


class Tool(Base):
    def test_render_chart_publishes_a_chart_card_with_a_stored_picture(self):
        said = charts.render_chart(BAR)
        self.assertIn("Showing a bar chart", said)
        card = self.sent[-1]
        self.assertEqual((card["type"], card["kind"], card["turn"]), ("panel.chart", "bar", 9))
        self.assertTrue(self.files.local_path(card["image"]).exists())
        self.assertEqual(card["labels"][0], "Burj Khalifa")
        self.assertEqual(card["series"][0]["values"], [828.0, 679.0, 632.0])
        self.assertEqual(pn.validate(card), [])

    def test_render_chart_table_card(self):
        self.assertIn("table with 2 rows", charts.render_chart(TABLE))
        card = self.sent[-1]
        self.assertEqual((card["type"], card["columns"]), ("panel.table", ["Building", "Metres"]))
        self.assertEqual(pn.validate(card), [])

    def test_failures_are_spoken_not_raised(self):
        self.assertIn("couldn't make that chart", charts.render_chart({**BAR, "kind": "radar"}))
        self.assertEqual(self.sent, [])
        charts.set_panel(None, None)
        self.assertIn("isn't available", charts.render_chart(BAR))

    def test_the_model_tool_goes_through_the_dispatcher_with_array_arguments(self):
        d = Dispatcher(dict(tools.REGISTRY), tools.SCHEMAS)
        d.begin_turn()
        out = d.dispatch("render_chart", BAR)
        self.assertEqual((out["agent"], out["code"]), ("charts", "completed"), out)
        self.assertEqual(self.sent[-1]["type"], "panel.chart")
        d.begin_turn()
        bad = d.dispatch("render_chart", {**BAR, "labels": "not a list"})
        self.assertNotEqual(bad["code"], "completed")

    def test_the_tool_is_offered_for_chart_requests_only(self):
        names = lambda text: {s["function"]["name"] for s in tools.select_schemas(text)}  # noqa: E731
        self.assertIn("render_chart", names("make a bar chart of the tallest buildings"))
        self.assertIn("render_chart", names("show that as a table"))
        self.assertNotIn("render_chart", names("what's the weather in Springfield"))


class Gallery(Base):
    RESULT = {
        "question": "How tall is the Burj Khalifa?",
        "summary": "828 m.",
        "findings": [],
        "sources": [],
        "images": [],
    }

    def card_with_images(self, n=3):
        imgs = []
        for i in range(n):
            buf = io.BytesIO()
            Image.new("RGB", (400, 300), (i * 40, 90, 160)).save(buf, "PNG")
            data, ext = pf.ingest_image(buf.getvalue())
            imgs.append(
                {
                    "image": self.files.put(data, ext),
                    "alt": f"photo {i}",
                    "page_url": "https://a.example/p",
                    "page_title": "A page",
                }
            )
        return self.panel.research(self.RESULT, imgs, turn=4)

    def test_show_the_images_makes_a_gallery_card_from_the_research_images(self):
        self.card_with_images(3)
        self.assertEqual(pe.handle(self.panel, None, "show the images"), "Showing 3 images.")
        g = self.sent[-1]
        self.assertEqual((g["type"], len(g["images"]), g["turn"]), ("panel.images", 3, 4))
        self.assertTrue(g["title"].startswith("Images: How tall"))
        self.assertEqual(pn.validate(g), [])
        for phrase in (
            "Show me the pictures",
            "please show the photos from the research on the Burj Khalifa.",
        ):
            self.assertTrue(pe.handle(self.panel, None, phrase).startswith("Showing"), phrase)

    def test_no_images_or_no_research_card_is_said_plainly(self):
        self.assertEqual(
            pe.handle(self.panel, None, "show the images"),
            "I don't have a research card to show images from.",
        )
        self.panel.research(self.RESULT, [], turn=1)
        self.assertEqual(
            pe.handle(self.panel, None, "show the images"), "That research has no images."
        )
        self.assertIsNone(
            pe.handle(self.panel, None, "show the images of the moon landing")
        )  # not a panel command


if __name__ == "__main__":
    unittest.main()
