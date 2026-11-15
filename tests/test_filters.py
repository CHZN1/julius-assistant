"""Stage 5c-art step C: the photo style filters (cel-shade, comic, pencil, pop art, watercolor) and their card."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

import julius.ui_bridge.charts as charts
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panel_emit as pe
import julius.ui_bridge.panelfiles as pf
from julius.art import filters, source
from julius.art import tool as art_tool


def photo(size=(240, 180)) -> Image.Image:
    rng = np.random.default_rng(3)
    base = np.zeros((size[1], size[0], 3), dtype=np.float32)
    base[..., 0] = np.linspace(60, 220, size[0])[None, :]
    base[..., 1] = np.linspace(180, 80, size[1])[:, None]
    base[..., 2] = 120
    im = Image.fromarray(np.clip(base + rng.normal(0, 9, base.shape), 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    d.ellipse((70, 40, 170, 150), fill=(210, 150, 120), outline=(30, 20, 20), width=3)
    d.rectangle((95, 80, 145, 95), fill=(30, 30, 40))
    return im


def grad_energy(im: Image.Image) -> float:
    g = np.asarray(im.convert("L"), dtype=np.float32)
    return float(np.abs(np.diff(g, axis=1)).mean() + np.abs(np.diff(g, axis=0)).mean())


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.src = photo()

    def test_every_style_returns_a_picture_of_the_same_shape_and_is_deterministic(self):
        for st in filters.STYLES:
            a, s = filters.apply(self.src, filters.FilterSettings(st))
            b, _ = filters.apply(self.src, filters.FilterSettings(st))
            self.assertEqual(a.size, self.src.size, st)
            self.assertEqual(a.tobytes(), b.tobytes(), st)
            self.assertNotEqual(a.tobytes(), self.src.convert("RGB").tobytes(), st)
            self.assertEqual(s.style, st)

    def test_big_pictures_are_worked_at_a_capped_size(self):
        out, _ = filters.apply(
            Image.new("RGB", (3000, 1500), (90, 120, 150)), filters.FilterSettings("pencil")
        )
        self.assertEqual(max(out.size), filters.WORK_SIDE)

    def test_settings_are_clamped_and_defaulted_per_style(self):
        s = filters.FilterSettings("cel_shade", colors=999, line=99, smooth=99).clean()
        self.assertEqual((s.colors, s.line, s.smooth), (32, 5, 5))
        d = filters.FilterSettings("watercolor").clean()
        self.assertEqual((d.colors, d.line, d.smooth), filters.DEFAULTS["watercolor"])
        self.assertEqual(filters.FilterSettings("pencil").clean().colors, 0)
        with self.assertRaises(Exception):
            filters.FilterSettings("oil").clean()

    def test_cel_shade_has_few_flat_colours_plus_ink(self):
        out, _ = filters.apply(self.src, filters.FilterSettings("cel_shade", colors=5))
        n = len({tuple(p) for p in np.asarray(out).reshape(-1, 3)})
        self.assertLessEqual(
            n, 5 + 1 + 40
        )  # the colours, the ink, and the odd blend where the quantiser rounds

    def test_more_line_weight_means_more_ink(self):
        thin, _ = filters.apply(self.src, filters.FilterSettings("cel_shade", line=1))
        thick, _ = filters.apply(self.src, filters.FilterSettings("cel_shade", line=5))
        ink = lambda im: int((np.asarray(im).sum(axis=2) < 90).sum())  # noqa: E731
        self.assertGreater(ink(thick), ink(thin))

    def test_pencil_is_light_paper_with_dark_strokes(self):
        out, _ = filters.apply(self.src, filters.FilterSettings("pencil"))
        g = np.asarray(out.convert("L"))
        self.assertGreater(g.mean(), 170)
        self.assertLess(g.min(), 120)

    def test_pop_art_uses_the_bold_palette_only(self):
        out, s = filters.apply(self.src, filters.FilterSettings("pop_art", colors=4))
        used = {tuple(p) for p in np.asarray(out).reshape(-1, 3)}
        self.assertLessEqual(len(used), 4)
        self.assertTrue(used <= {tuple(c) for c in filters.POP})

    def test_watercolor_is_smoother_than_the_photo(self):
        out, _ = filters.apply(self.src, filters.FilterSettings("watercolor"))
        self.assertLess(grad_energy(out), grad_energy(self.src))

    def test_comic_has_halftone_dots_in_the_shadows(self):
        dark = Image.new("RGB", (160, 160), (70, 70, 80))
        out, _ = filters.apply(dark, filters.FilterSettings("comic"))
        g = np.asarray(out.convert("L"), dtype=np.float32)
        self.assertGreater(g.std(), 8)  # a flat dark area is dotted, not flat

    def test_transparent_areas_become_paper(self):
        im = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((20, 20, 60, 60), fill=(200, 60, 60, 255))
        out, _ = filters.apply(im, filters.FilterSettings("pencil"))
        self.assertGreater(np.asarray(out.convert("L"))[2, 2], 200)


class FilterToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.mine = self.root / "Pictures"
        self.mine.mkdir()
        photo().save(self.mine / "me.png")
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.root / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        charts.set_panel(self.panel, self.files, lambda: 5)
        self.addCleanup(charts.set_panel, None, None)
        self.addCleanup(self.tmp.cleanup)

    def run_style(self, style, **kw):
        with mock.patch.object(
            source.config, "load_config", return_value={"art": {"folders": [str(self.mine)]}}
        ):
            return art_tool.image_style("me", style, **kw)

    def test_each_style_makes_a_valid_card_and_says_the_facts(self):
        for st in filters.STYLES:
            said = self.run_style(st)
            card = self.sent[-1]
            self.assertEqual(pn.validate(card), [], st)
            self.assertEqual((card["type"], card["kind"], card["turn"]), ("panel.art", "filter", 5))
            self.assertIn(filters.LABELS[st], said)
            self.assertIn("side panel", said)
        self.assertEqual([p for p in (self.root / "p").iterdir() if p.is_file()], [])

    def test_words_pick_the_style_and_settings_reach_the_filter(self):
        said = self.run_style("pencil sketch", line=4, smooth=2)
        self.assertIn("Pencil sketch", said)
        self.assertIn("line weight 4, smoothing 2", said)
        self.run_style("comic book", colors=9)
        self.assertIn("Colours: 9", self.sent[-1]["settings"])

    def test_pixel_art_still_gets_its_default_colours(self):
        said = self.run_style("pixel_art")
        self.assertIn("Pixel art from me.png", said)

    def test_the_picture_must_be_given_and_nothing_is_drawn_from_nothing(self):
        self.assertIn("Which picture", art_tool.image_style("", "comic"))

    def test_save_this_card_writes_the_restyled_picture_only(self):
        self.run_style("cel_shade")
        saved = self.root / "Julius Saved"
        self.assertIn("Saved to", pe.save_card(self.panel, self.files, str(saved)))
        (folder,) = list(saved.iterdir())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["card.md", "restyled.png"])


if __name__ == "__main__":
    unittest.main()
