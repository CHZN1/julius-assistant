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

    def test_photos_libraries_are_never_read_in_place(self):
        lib = self.mine / "Photos Library.photoslibrary" / "originals" / "A"
        lib.mkdir(parents=True)
        p = picture(lib / "IMG_0001.png")
        picture(self.mine / "export.png")
        with cfg(self.mine):
            with self.assertRaises(source.ArtProblem) as c:
                source.check_path(p)
            self.assertIn("Photos library", str(c.exception))
            with self.assertRaises(source.ArtProblem):
                source.resolve("IMG_0001")  # a name search does not walk into the library
            self.assertEqual(source.resolve("export")[0], self.mine / "export.png")
        with cfg(
            self.mine / "Photos Library.photoslibrary"
        ):  # listing the library itself as an art folder changes nothing
            self.assertEqual(source.folders(), [])
        (self.mine / "Old.aplibrary").mkdir()
        q = picture(self.mine / "Old.aplibrary" / "x.png")
        with cfg(self.mine), self.assertRaises(source.ArtProblem):
            source.check_path(q)
        link = self.mine / "viaLink.png"
        link.symlink_to(p)
        with cfg(self.mine), self.assertRaises(source.ArtProblem):
            source.check_path(link)

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
        patch = mock.patch.object(
            art_tool.faces, "detect", return_value=[]
        )  # no real helper in tests
        self.detect = patch.start()
        self.addCleanup(patch.stop)

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


class PortraitTests(unittest.TestCase):
    """Face crop, preprocessing, luminance mapping, dithering, strong-edge outline and the comparison grid."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.mine = self.root / "Pictures"
        self.mine.mkdir()
        im = Image.new(
            "RGB", (1200, 900), (235, 230, 220)
        )  # a flat, bright wall: the case that came out as mush
        d = ImageDraw.Draw(im)
        d.ellipse((450, 200, 750, 560), fill=(225, 180, 150))  # head
        d.rectangle((300, 560, 900, 900), fill=(190, 190, 200))  # shoulders
        d.ellipse((520, 330, 560, 360), fill=(60, 40, 30))
        d.ellipse((640, 330, 680, 360), fill=(60, 40, 30))
        im.save(self.mine / "me.png")
        self.face = (450 / 1200, 200 / 900, 300 / 1200, 360 / 900)
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.root / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        charts.set_panel(self.panel, self.files, lambda: 4)
        self.addCleanup(charts.set_panel, None, None)
        self.addCleanup(self.tmp.cleanup)

    def run_tool(self, face=True, **kw):
        with (
            cfg(self.mine),
            mock.patch.object(art_tool.faces, "detect", return_value=[self.face] if face else []),
        ):
            return art_tool.pixel_art("me", **kw)

    def test_the_crop_is_head_and_shoulders_with_margin_and_stays_inside_the_picture(self):
        from julius.art import faces

        box = faces.head_and_shoulders((1200, 900), self.face)
        left, top, right, bottom = box
        self.assertTrue(0 <= left < 450 and right > 750, box)  # wider than the face
        self.assertTrue(top < 200 and bottom > 560, box)  # room above and below
        edge = faces.head_and_shoulders((1200, 900), (0.9, 0.7, 0.2, 0.3))
        self.assertTrue(0 <= edge[0] and edge[2] <= 1200 and 0 <= edge[1] and edge[3] <= 900)

    def test_no_face_or_no_helper_means_the_whole_picture(self):
        from julius.art import faces

        im = Image.new("RGB", (100, 80))
        with mock.patch.object(faces, "detect", return_value=[]):
            out, found, focus = faces.portrait_crop(im)
        self.assertEqual((out.size, found, focus), ((100, 80), False, None))
        boom = mock.Mock(side_effect=OSError("no helper"))
        self.assertEqual(faces.detect(im, runner=boom, binary=Path("/nope")), [])

    def test_the_helper_gets_the_picture_on_stdin_and_the_largest_face_wins(self):
        from julius.art import faces

        out = mock.Mock(stdout=b'{"w":10,"h":10,"faces":[[0.4,0.3,0.3,0.4],[0.1,0.1,0.02,0.02]]}\n')
        runner = mock.Mock(return_value=out)
        got = faces.detect(Image.new("RGB", (50, 50)), runner=runner, binary=Path("/x/visiond"))
        self.assertEqual(got, [(0.4, 0.3, 0.3, 0.4)])  # the tiny one is dropped
        args, kw = runner.call_args
        self.assertEqual(args[0], ["/x/visiond", "faces"])
        self.assertTrue(kw["input"].startswith(b"\xff\xd8"))  # a JPEG, sent on stdin: no file

    def test_a_portrait_defaults_to_64_wide_caps_at_128_and_full_photo_skips_the_crop(self):
        said = self.run_tool()
        self.assertIn("cropped to head and shoulders", said)
        card = self.sent[-1]
        self.assertTrue(any(x == "Crop: head and shoulders" for x in card["settings"]))
        self.assertIn(
            "Look: retro sprite", card["settings"]
        )  # a portrait defaults to the retro look
        self.assertIn("Outline: on", card["settings"])  # which includes the bold outline
        self.run_tool(retro=False)
        self.assertIn("Look: smooth", self.sent[-1]["settings"])
        self.run_tool(face=False)
        self.assertIn("Look: smooth", self.sent[-1]["settings"])  # no face, no retro default
        self.assertIn("64 by", said)
        self.assertIn("128 by", self.run_tool(size=500))
        said = self.run_tool(full=True)
        self.assertNotIn("cropped", said)
        self.assertIn("48 by 36", said)
        self.assertTrue(any(x == "Crop: full photo" for x in self.sent[-1]["settings"]))
        self.assertIn("48 by 36", self.run_tool(face=False))

    def test_a_flat_low_contrast_photo_uses_all_four_gameboy_tones(self):
        from julius.art import pixel

        flat = Image.effect_noise((200, 200), 6).convert("RGB")
        flat = Image.blend(flat, Image.new("RGB", (200, 200), (200, 190, 180)), 0.9)
        d = ImageDraw.Draw(flat)
        d.ellipse((60, 60, 140, 150), fill=(190, 150, 130))
        r = pixel.render(flat, pixel.Settings(size=48, palette="gameboy"))
        self.assertEqual(len(r.palette), 4)  # a naive nearest-colour mapping would give one or two

    def test_dither_is_ordered_deterministic_and_changes_the_pixels(self):
        from julius.art import pixel

        ramp = Image.linear_gradient("L").resize((128, 64)).convert("RGB")
        a = pixel.render(ramp, pixel.Settings(size=64, palette="gameboy"))
        b = pixel.render(ramp, pixel.Settings(size=64, palette="gameboy", dither=True))
        c = pixel.render(ramp, pixel.Settings(size=64, palette="gameboy", dither=True))
        self.assertEqual(b.small.tobytes(), c.small.tobytes())
        self.assertNotEqual(a.small.tobytes(), b.small.tobytes())

    def test_the_outline_is_only_on_strong_edges_one_pixel_and_the_darkest_colour(self):
        import numpy as np

        from julius.art import pixel

        soft = Image.linear_gradient("L").resize((96, 96)).convert("RGB")  # no edge anywhere
        r = pixel.render(soft, pixel.Settings(size=48, palette="gameboy", outline=True))
        plain = pixel.render(soft, pixel.Settings(size=48, palette="gameboy"))
        self.assertEqual(r.small.tobytes(), plain.small.tobytes())
        hard = Image.new("RGB", (96, 96), (240, 240, 240))
        ImageDraw.Draw(hard).rectangle((30, 30, 66, 66), fill=(20, 20, 20))
        r = pixel.render(hard, pixel.Settings(size=48, palette="gameboy", outline=True))
        a = np.asarray(r.small)[..., :3].reshape(-1, 3)
        ink = np.array([0x0F, 0x38, 0x0F])
        marked = (a == ink).all(axis=1).sum()
        self.assertGreater(marked, 0)
        edge_len = 4 * 36 * 48 / 96
        self.assertLess(
            marked, edge_len * 3 + 400
        )  # a thin line plus the dark square, not a fat border

    def test_the_comparison_grid_is_nine_labelled_variants_in_one_card(self):
        said = self.run_tool(compare=True)
        card = self.sent[-1]
        self.assertEqual(pn.validate(card), [])
        self.assertEqual(card["kind"], "compare")
        self.assertIn("32, 48 and 64", said)
        data, _ = self.files.bytes_of(card["after"])
        im = Image.open(__import__("io").BytesIO(data))
        self.assertGreater(im.width, 3 * 150)
        self.assertGreater(im.height, 2 * 150)
        self.assertEqual(
            [p for p in (self.root / "p").iterdir() if p.is_file()], []
        )  # nothing on disk


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
                "dither": False,
                "full": False,
                "compare": False,
            },
        )
        d = art_tool.parse_options("")
        self.assertEqual(
            d,
            {
                "outline": False,
                "transparent": False,
                "dither": False,
                "full": False,
                "compare": False,
            },
        )
        self.assertTrue(art_tool.parse_options("make it retro")["retro"])
        self.assertFalse(art_tool.parse_options("smooth, 64 pixels")["retro"])
        self.assertNotIn("retro", art_tool.parse_options("64 pixels"))
        self.assertTrue(art_tool.parse_options("gameboy, dither on")["dither"])
        self.assertFalse(art_tool.parse_options("gameboy, dither off")["dither"])
        self.assertFalse(art_tool.parse_options("no dithering please")["dither"])
        self.assertTrue(art_tool.parse_options("full photo, 64 pixels")["full"])
        self.assertTrue(art_tool.parse_options("show me a comparison grid")["compare"])
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

    def test_image_style_is_a_model_tool_that_only_restyles_a_given_picture(self):
        import julius.brain.tools as tools

        self.assertIn("image_style", tools.REGISTRY)
        schema = next(x for x in tools.SCHEMAS if x["function"]["name"] == "image_style")
        desc = schema["function"]["description"]
        self.assertIn("never creates art from scratch", desc)
        self.assertEqual(
            set(schema["function"]["parameters"]["properties"]),
            {
                "source",
                "style",
                "size",
                "colors",
                "palette",
                "outline",
                "transparent",
                "zoom",
                "dither",
                "full_photo",
                "compare",
                "retro",
                "line",
                "smooth",
                "head",
                "chibi",
                "big_eyes",
                "nose",
            },
        )
        self.assertEqual(schema["function"]["parameters"]["required"], ["source", "style"])

    def test_style_names_in_any_words_map_to_pixel_art_and_unknown_styles_are_declined(self):
        for t in (
            "pixel_art",
            "pixel art",
            "8-bit",
            "16 bit",
            "Game Boy",
            "",
            "pixelated",
        ):
            self.assertEqual(art_tool.style_name(t), "pixel_art", t)
        for t in ("oil painting", "stained glass", "vaporwave"):
            self.assertEqual(art_tool.style_name(t), "", t)
            self.assertIn("only do these styles so far", art_tool.image_style("x.png", t))
        for t, want in (
            ("comic book", "comic"),
            ("halftone ink", "comic"),
            ("pencil sketch", "pencil"),
            ("pop art", "pop_art"),
            ("watercolour", "watercolor"),
            ("cartoon", "cel_shade"),
            ("cel shaded", "cel_shade"),
            ("retro sprite", "sprite"),
            ("game sprite", "sprite"),
        ):
            self.assertEqual(art_tool.style_name(t), want, t)

    def test_image_style_with_no_picture_asks_for_one_and_never_draws(self):
        source.set_drop_source(lambda: None)
        self.addCleanup(source.set_drop_source, None)
        with cfg(Path(tempfile.gettempdir())):
            out = art_tool.image_style("", "pixel_art")
        self.assertIn("Which picture", out)

    def test_image_style_makes_the_same_pixels_as_the_command_path(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t).resolve()
            picture(root / "me.png")
            with cfg(root):
                a = art_tool.image_style("me.png", "pixel art", 24, 4, "", False, False, 4)
                b = art_tool.pixel_art("me.png", 24, 4, "", False, False, 4)
        self.assertEqual(a, b)
        self.assertIn("24 by 18 pixels", a)

    def test_shipped_config_lists_art_folders_and_none_is_an_asset_pack(self):
        import julius.config as c

        folders = c.load_config()["art"]["folders"]
        self.assertTrue(folders)
        for f in folders:
            self.assertIsNone(source._PACK_NAME.search(Path(f).name), f)


if __name__ == "__main__":
    unittest.main()


class RoutingCaseFlags(unittest.TestCase):
    def test_fragile_flags_exist_and_cover_the_known_knife_edge_cases(self):
        import json

        from julius.paths import REPO_ROOT

        cases = [
            json.loads(x)
            for x in (REPO_ROOT / "evals" / "routing_cases.jsonl").read_text().splitlines()
            if x.strip()
        ]
        ids = {c["id"] for c in cases}
        flagged = {c["id"] for c in cases if c.get("fragile")}
        self.assertLessEqual({"r101", "r129"}, flagged)
        self.assertTrue(flagged <= ids)
        for natural in ("r208", "r209", "r210", "r211", "r212"):
            self.assertIn(natural, flagged)
        self.assertTrue((REPO_ROOT / "evals" / "routing_shake.py").exists())
