"""Stage 5c-art A+B: the image-source rules (dropped or approved folders only, never asset packs), photo to pixel art, the card and "save this card"."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

import julius.ui_bridge.charts as charts
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panel_emit as pe
import julius.ui_bridge.panelfiles as pf
from julius.art import pixel, source
from julius.art import tool as art_tool


def picture(path: Path, size=(400, 300)) -> Path:
    im = Image.new("RGB", size, (40, 90, 200))
    d = ImageDraw.Draw(im)
    d.ellipse((100, 60, 300, 240), fill=(220, 40, 40))
    d.rectangle((180, 120, 220, 200), fill=(250, 230, 60))
    im.save(path)
    return path


def cfg(*folders):
    return mock.patch.object(
        source.config, "load_config", return_value={"art": {"folders": [str(f) for f in folders]}}
    )


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.mine = self.root / "Pictures"
        self.mine.mkdir()
        self.img = picture(self.mine / "me.png")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(source.set_drop_source, None)

    def test_a_file_in_an_approved_folder_opens_by_name_or_path(self):
        with cfg(self.mine):
            self.assertEqual(source.resolve("me")[0], self.img)
            self.assertEqual(source.resolve("me.png")[0], self.img)
            self.assertEqual(source.resolve(str(self.img))[0], self.img)

    def test_outside_the_approved_folders_is_refused(self):
        other = picture(self.root / "elsewhere.png")
        with cfg(self.mine):
            with self.assertRaises(source.ArtProblem) as c:
                source.resolve(str(other))
            self.assertIn("isn't in a folder I'm approved", str(c.exception))
            with self.assertRaises(source.ArtProblem):
                source.resolve("elsewhere")

    def test_a_symlink_out_of_an_approved_folder_is_refused(self):
        other = picture(self.root / "secret.png")
        (self.mine / "link.png").symlink_to(other)
        with cfg(self.mine), self.assertRaises(source.ArtProblem):
            source.resolve(str(self.mine / "link.png"))

    def test_asset_pack_folders_are_never_used(self):
        for name in ("Unity Asset Store", "Purchased Sprites", "Kenney Pixel Pack", "stock photos"):
            d = self.mine / name
            d.mkdir()
            p = picture(d / "hero.png")
            with cfg(self.mine):
                with self.assertRaises(source.ArtProblem) as c:
                    source.resolve(str(p))
                self.assertIn("asset pack", str(c.exception), name)
                with self.assertRaises(source.ArtProblem):
                    source.resolve("hero")  # a name search does not even look inside

    def test_a_folder_with_a_licence_file_is_treated_as_a_licensed_pack(self):
        d = self.mine / "Cool Dungeon Tiles"
        d.mkdir()
        p = picture(d / "wall.png")
        with cfg(self.mine):
            self.assertEqual(source.check_path(p), p)
            (d / "LICENSE.txt").write_text("not for redistribution")
            with self.assertRaises(source.ArtProblem) as c:
                source.check_path(p)
            self.assertIn("licen", str(c.exception))

    def test_a_licence_file_next_to_loose_files_in_the_approved_root_does_not_block_them(self):
        (self.mine / "LICENSE").write_text("x")
        with cfg(self.mine):
            self.assertEqual(source.check_path(self.img), self.img)

    def test_protected_music_locations_are_refused_even_if_listed(self):
        logic = self.mine / "Logic"
        logic.mkdir()
        p = picture(logic / "cover.png")
        with cfg(self.mine), self.assertRaises(source.ArtProblem):
            source.check_path(p)

    def test_non_images_and_ambiguous_names(self):
        (self.mine / "notes.txt").write_text("hi")
        sub = self.mine / "b"
        sub.mkdir()
        picture(sub / "me.png")
        with cfg(self.mine):
            with self.assertRaises(source.ArtProblem) as c:
                source.resolve("me")
            self.assertIn("AMBIGUOUS", str(c.exception))
            with self.assertRaises(source.ArtProblem):
                source.check_path(self.mine / "notes.txt")

    def test_a_dropped_image_is_used_as_is_and_needs_no_folder(self):
        other = picture(self.root / "dropped.png")
        source.set_drop_source(lambda: {"items": [{"kind": "file", "path": str(other)}]})
        with cfg(self.mine):
            path, dropped = source.resolve("dropped")
        self.assertEqual((path, dropped), (other, True))
        source.set_drop_source(lambda: None)
        with cfg(self.mine), self.assertRaises(source.ArtProblem):
            source.resolve("")

    def test_open_image_rejects_junk_and_never_modifies_the_original(self):
        junk = self.mine / "broken.png"
        junk.write_bytes(b"not an image")
        before = self.img.read_bytes()
        with self.assertRaises(source.ArtProblem):
            source.open_image(junk)
        im = source.open_image(self.img)
        self.assertEqual((im.mode, im.size), ("RGBA", (400, 300)))
        self.assertEqual(self.img.read_bytes(), before)


class PixelTests(unittest.TestCase):
    def setUp(self):
        self.src = Image.new("RGB", (400, 300), (40, 90, 200))
        d = ImageDraw.Draw(self.src)
        d.ellipse((100, 60, 300, 240), fill=(220, 40, 40))
        d.rectangle((180, 120, 220, 200), fill=(250, 230, 60))

    def test_size_keeps_the_aspect_and_colours_are_capped(self):
        r = pixel.render(self.src, pixel.Settings(size=32, colors=6))
        self.assertEqual((r.width, r.height), (32, 24))
        self.assertLessEqual(len(r.palette), 6)
        self.assertFalse(r.has_alpha)

    def test_a_fixed_palette_uses_only_its_colours(self):
        for name in ("gameboy", "pico8", "c64", "1bit"):
            r = pixel.render(self.src, pixel.Settings(size=24, palette=name))
            self.assertLessEqual(set(r.palette), set(pixel.PALETTES[name]), name)
        self.assertEqual(pixel.palette_name("Game Boy"), "gameboy")
        with self.assertRaises(pixel.PixelProblem):
            pixel.palette_name("neon")

    def test_the_same_input_gives_the_same_pixels(self):
        a = pixel.render(self.src, pixel.Settings(size=40, colors=8))
        b = pixel.render(self.src, pixel.Settings(size=40, colors=8))
        self.assertEqual(a.small.tobytes(), b.small.tobytes())

    def test_export_is_a_crisp_nearest_neighbour_zoom(self):
        r = pixel.render(self.src, pixel.Settings(size=16, colors=4, scale=8))
        big, k = r.scaled()
        self.assertEqual((k, big.size), (8, (r.width * 8, r.height * 8)))
        px = r.small.load()
        bg = big.load()
        for y in range(r.height):
            for x in range(r.width):
                block = {bg[x * 8 + dx, y * 8 + dy] for dx in (0, 7) for dy in (0, 7)}
                self.assertEqual(block, {px[x, y]})
        self.assertEqual(
            len({c for _, c in big.getcolors(10**6)}), len(r.palette)
        )  # no blended colours

    def test_scale_snaps_to_1_2_4_8_and_is_capped_by_the_export_limit(self):
        self.assertEqual(pixel.Settings(scale=3).clean().scale, 2)
        self.assertEqual(pixel.Settings(scale=100).clean().scale, 8)
        self.assertEqual(pixel.Settings(size=9999, colors=1).clean().size, pixel.MAX_SIZE)
        self.assertEqual(pixel.Settings(colors=1).clean().colors, pixel.MIN_COLORS)
        r = pixel.render(self.src, pixel.Settings(size=256, scale=8))
        big, k = r.scaled()
        self.assertLessEqual(max(big.size), pixel.MAX_EXPORT_SIDE)
        self.assertEqual(k, 8)

    def test_transparent_background_cuts_out_the_border_colour_only(self):
        r = pixel.render(self.src, pixel.Settings(size=32, colors=4, transparent=True))
        self.assertTrue(r.has_alpha)
        px = r.small.load()
        self.assertEqual(px[0, 0][3], 0)  # the corner (background) is clear
        self.assertEqual(px[r.width // 2, r.height // 2][3], 255)  # the subject stays
        data, _ = r.png()
        self.assertEqual(Image.open(__import__("io").BytesIO(data)).mode, "RGBA")

    def test_a_one_colour_picture_is_not_erased(self):
        flat = Image.new("RGB", (50, 50), (10, 10, 10))
        r = pixel.render(flat, pixel.Settings(size=8, transparent=True))
        self.assertFalse(r.has_alpha)

    def test_outline_wraps_a_cut_out_subject_in_one_dark_pixel(self):
        plain = pixel.render(self.src, pixel.Settings(size=32, colors=4, transparent=True))
        lined = pixel.render(
            self.src, pixel.Settings(size=32, colors=4, transparent=True, outline=True)
        )
        self.assertEqual((lined.width, lined.height), (plain.width + 2, plain.height + 2))
        self.assertGreater(lined.small.getchannel("A").getbbox()[2], 0)
        a = lined.small.getchannel("A")
        self.assertGreater(
            sum(1 for v in a.getdata() if v),
            sum(1 for v in plain.small.getchannel("A").getdata() if v),
        )

    def test_a_fully_transparent_picture_is_refused(self):
        clear = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        with self.assertRaises(pixel.PixelProblem):
            pixel.render(clear, pixel.Settings())

    def test_transparent_input_keeps_its_own_cutout(self):
        im = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((10, 10, 50, 50), fill=(200, 30, 30, 255))
        r = pixel.render(im, pixel.Settings(size=20, colors=4))
        self.assertTrue(r.has_alpha)
        self.assertEqual(r.small.load()[0, 0][3], 0)


class CardAndSaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.mine = self.root / "Pictures"
        self.mine.mkdir()
        picture(self.mine / "tree.png")
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.root / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        charts.set_panel(self.panel, self.files, lambda: 4)
        self.addCleanup(charts.set_panel, None, None)
        self.addCleanup(self.tmp.cleanup)

    def run_tool(self, **kw):
        with cfg(self.mine):
            return art_tool.pixel_art("tree", **kw)

    def test_the_card_validates_holds_pictures_in_memory_and_says_the_facts(self):
        said = self.run_tool(size=32, colors=8, scale=4)
        card = self.sent[-1]
        self.assertEqual(pn.validate(card), [])
        self.assertEqual((card["type"], card["kind"], card["turn"]), ("panel.art", "pixel", 4))
        self.assertIn("32 by 24 pixels", said)
        self.assertIn("crisp", said)
        self.assertEqual(
            [p for p in (self.root / "p").iterdir() if p.is_file()], []
        )  # nothing on disk yet
        self.assertEqual(
            {k: card[k][-4:] for k in ("before", "after", "export", "small")},
            {k: ".png" for k in ("before", "after", "export", "small")},
        )

    def test_save_this_card_writes_the_export_and_1x_and_a_note_and_never_the_original(self):
        self.run_tool(size=16, colors=4, scale=8, transparent=True)
        saved = self.root / "Julius Saved"
        out = pe.save_card(self.panel, self.files, str(saved))
        self.assertIn("Saved to", out)
        (folder,) = list(saved.iterdir())
        names = sorted(p.name for p in folder.iterdir())
        self.assertEqual(names, ["card.md", "pixel-art-1x.png", "pixel-art.png"])
        big = Image.open(folder / "pixel-art.png")
        small = Image.open(folder / "pixel-art-1x.png")
        self.assertEqual(big.size, (small.size[0] * 8, small.size[1] * 8))
        self.assertEqual(big.mode, "RGBA")
        self.assertIn("only read", (folder / "card.md").read_text())

    def test_problems_come_back_as_sentences_not_exceptions(self):
        with cfg(self.mine):
            self.assertIn("couldn't find", art_tool.pixel_art("nothing_like_it"))
            self.assertIn("Which picture", art_tool.pixel_art(""))
            self.assertIn("palette", art_tool.pixel_art("tree", palette="neon"))
        self.assertEqual(len(self.sent), 0)

    def test_no_panel_means_nothing_to_save_but_still_an_answer(self):
        charts.set_panel(None, None)
        said = self.run_tool(size=16)
        self.assertIn("No side panel", said)


class CommandTests(unittest.TestCase):
    def test_requests_for_pixel_art_are_recognised(self):
        from julius.art import commands as c

        for text, src in [
            ("Turn this photo I just dropped on the orb into pixel art.", "dropped"),
            ("Make sunset.jpg into 32 pixel art with the gameboy palette.", "sunset.jpg"),
            (
                "Pixelate the picture I just dropped, 64 pixels, 8 colors, with an outline.",
                "dropped",
            ),
            ("Can you make profile.png look like an 8-bit sprite?", "profile.png"),
            ("hey julius, convert ~/Pictures/me.jpeg to pixel art", "~/Pictures/me.jpeg"),
            ("pixelate this", "dropped"),
        ]:
            got = c.parse(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got["source"], src, text)

    def test_other_requests_are_left_alone(self):
        from julius.art import commands as c

        for text in [
            "What is pixel art?",
            "How do I make pixel art in Aseprite?",
            "Make the window 800 pixels wide.",
            "Convert kick.wav to 16 bit.",
            "Make this loop 16 bit.",
            "Draw me a picture of a dragon.",
            "What's the BPM of pixel_dust.wav?",
            "Explain 8-bit sprite palettes.",
        ]:
            self.assertIsNone(c.parse(text), text)

    def test_asking_julius_to_draw_one_from_nothing_is_declined_with_what_it_can_do(self):
        from julius.art import commands as c

        for text in ["Make me a pixel art dragon.", "Create a pixel art castle for my game."]:
            got = c.parse(text)
            self.assertTrue(got and got.get("generate"), text)
            self.assertIn("don't draw or generate", c.handle(text))

    def test_options_are_read_from_words_and_default_when_absent(self):
        o = art_tool.parse_options(
            "32 pixels, 8 colors, game boy palette, outlined, transparent background, 8x"
        )
        self.assertEqual(
            o,
            {
                "size": 32,
                "colors": 8,
                "scale": 8,
                "palette": "gameboy",
                "outline": True,
                "transparent": True,
            },
        )
        d = art_tool.parse_options("")
        self.assertEqual(d, {"outline": False, "transparent": False})
        self.assertEqual(
            art_tool.parse_options("pico-8 palette, no outline, not transparent")["palette"],
            "pico8",
        )
        self.assertFalse(art_tool.parse_options("no outline")["outline"])
        self.assertEqual(art_tool.parse_options("make it 64 px")["size"], 64)

    def test_a_dropped_image_goes_through_the_command_end_to_end(self):
        from julius.art import commands as c

        with tempfile.TemporaryDirectory() as t:
            img = picture(Path(t) / "dropped.png")
            source.set_drop_source(lambda: {"items": [{"kind": "file", "path": str(img)}]})
            self.addCleanup(source.set_drop_source, None)
            out = c.handle("Pixelate the picture I just dropped, 24 pixels and 4 colors")
        self.assertIn("24 by 18 pixels", out)
        self.assertIn("4 colours", out)

    def test_it_is_a_spoken_command_not_a_model_tool(self):
        import julius.brain.tools as tools

        self.assertNotIn("pixel_art", tools.REGISTRY)

    def test_shipped_config_lists_art_folders_and_none_is_an_asset_pack(self):
        import julius.config as c

        folders = c.load_config()["art"]["folders"]
        self.assertTrue(folders)
        for f in folders:
            self.assertIsNone(source._PACK_NAME.search(Path(f).name), f)


if __name__ == "__main__":
    unittest.main()
