"""Stage 5c-art, Sprite mode: a game sprite built from measured landmarks and a person mask. Synthetic face and mask, so no Vision helper is needed."""

import io
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
from julius.art import faces, pixel, source, sprite
from julius.art import tool as art_tool

W, H = 400, 300
SKIN, HAIR_RGB, BG = (222, 170, 140), (40, 28, 20), (60, 110, 200)


def ellipse_pts(cx, cy, rx, ry, a0, a1, n):
    ts = np.linspace(np.radians(a0), np.radians(a1), n)
    return [[(cx + rx * np.cos(t)) / W, (cy + ry * np.sin(t)) / H] for t in ts]


BEARD_BOXES = {
    "full": (140, 182, 260, 240),
    "goatee": (182, 210, 218, 240),
    "mustache": (178, 182, 222, 198),
}


def synthetic(hair=True, beard=None):
    """A front-facing head: skin face, dark hair on top and a dark person mask with shoulders. Returns (image, face landmarks, mask)."""
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    m = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(m)
    md.rectangle((90, 250, 310, H), fill=255)  # shoulders
    d.rectangle((90, 250, 310, H), fill=(30, 30, 90))
    if hair:
        d.ellipse((120, 25, 280, 190), fill=HAIR_RGB)
        md.ellipse((120, 25, 280, 190), fill=255)
    d.ellipse((140, 80, 260, 235), fill=SKIN)
    md.ellipse((140, 80, 260, 235), fill=255)
    md.rectangle((175, 220, 225, 260), fill=255)
    d.rectangle((175, 220, 225, 260), fill=SKIN)
    if beard in BEARD_BOXES:
        d.rectangle(BEARD_BOXES[beard], fill=HAIR_RGB)
    # landmarks (fractions of the picture): jaw contour from the right ear round the chin to the left ear
    contour = ellipse_pts(200, 150, 60, 85, 20, 160, 17)
    contour = [[x, y] for x, y in contour]
    face = {
        "box": [140 / W, 80 / H, 120 / W, 155 / H],
        "roll": 0,
        "points": {
            "contour": contour,
            "left_eye": ellipse_pts(172, 140, 12, 4, 0, 360, 6),
            "right_eye": ellipse_pts(228, 140, 12, 4, 0, 360, 6),
            "left_brow": ellipse_pts(172, 124, 14, 3, 0, 360, 6),
            "right_brow": ellipse_pts(228, 124, 14, 3, 0, 360, 6),
            "nose": [[200 / W, 150 / H], [196 / W, 175 / H], [204 / W, 175 / H]],
            "mouth": [
                [180 / W, 200 / H],
                [200 / W, 204 / H],
                [220 / W, 200 / H],
                [200 / W, 208 / H],
            ],
            "inner_mouth": [[190 / W, 203 / H], [210 / W, 203 / H]],
        },
    }
    return im, face, m


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.img, self.face, self.mask = synthetic()

    def build(self, **kw):
        return sprite.build(self.img, self.face, self.mask, sprite.Options(**kw))

    def test_a_16_pixel_head_sits_on_a_32_canvas_with_a_transparent_background_and_a_one_pixel_outline(
        self,
    ):
        sp = self.build()
        self.assertEqual((sp.image.size, sp.image.mode), ((32, 32), "RGBA"))
        a = np.asarray(sp.image)
        ys, xs = np.nonzero(a[..., 3])
        self.assertLessEqual(ys.max() - ys.min() + 1, 16)
        self.assertLessEqual(xs.max() - xs.min() + 1, 16)
        self.assertEqual(a[0, 0, 3], 0)  # the corner is empty
        dark = sprite.map_colors(SKIN, HAIR_RGB, "gameboy")["line"]
        top = ys.min()
        row = a[top][a[top][:, 3] > 0]
        self.assertTrue(
            all(tuple(p[:3]) == dark for p in row),
            "the top row is the outline, in the darkest shade",
        )

    def test_it_is_deterministic(self):
        self.assertEqual(self.build().png(), self.build().png())

    def test_gameboy_uses_only_its_four_shades_and_skin_is_lighter_than_hair(self):
        sp = self.build(nose=True, big_eyes=True)
        gb = {c.upper() for c in pixel.PALETTES["gameboy"]}
        self.assertTrue(set(sp.palette) <= gb, sp.palette)

        def lum(h):
            return sum(int(h[i : i + 2], 16) * w for i, w in ((0, 0.299), (2, 0.587), (4, 0.114)))

        self.assertGreater(lum(sp.colors["skin"]), lum(sp.colors["hair"]))
        self.assertEqual(sp.colors["line"], "0F380F")

    def test_other_palettes_use_only_their_own_colours(self):
        for name in ("pico8", "nes"):
            sp = self.build(palette=name)
            allowed = {c.upper() for c in pixel.PALETTES[name]}
            used = {
                sprite._hex(tuple(int(v) for v in p[:3]))
                for p in np.asarray(sp.image).reshape(-1, 4)
                if p[3]
            }
            self.assertTrue(used <= allowed, (name, used - allowed))
            self.assertEqual(sp.colors["line"], "000000")

    def test_head_sizes_scale_the_canvas(self):
        for head in (16, 24, 32):
            sp = self.build(head=head)
            self.assertEqual(sp.image.size, (head * 2, head * 2))
            ys, xs = np.nonzero(np.asarray(sp.image)[..., 3])
            self.assertLessEqual(max(ys.max() - ys.min(), xs.max() - xs.min()) + 1, head)
        self.assertEqual(sprite.Options(head=20).clean().head, 16)
        self.assertEqual(sprite.Options(head=999).clean().head, 32)
        self.assertEqual(sprite.Options(head="x").clean().head, 16)

    def test_big_eyes_are_bigger_than_the_one_or_two_pixel_marks(self):
        img, face, mask = synthetic(
            hair=False
        )  # bald: every line-coloured pixel with opaque neighbours on all four sides is a feature, not the outline
        line = sprite.map_colors(SKIN, None, "gameboy")["line"]

        def features(**kw):
            a = np.asarray(sprite.build(img, face, mask, sprite.Options(**kw)).image)
            opaque, same = a[..., 3] > 0, (a[..., :3] == line).all(axis=2)
            inner = (
                opaque[1:-1, 1:-1]
                & opaque[:-2, 1:-1]
                & opaque[2:, 1:-1]
                & opaque[1:-1, :-2]
                & opaque[1:-1, 2:]
            )
            return int((inner & same[1:-1, 1:-1]).sum())

        self.assertGreater(features(big_eyes=True), features())

    def test_chibi_changes_proportions_and_it_still_validates(self):
        a, b = self.build(), self.build(chibi=True)
        self.assertNotEqual(a.png(), b.png())
        self.assertIn("chibi", " ".join(b.notes))

    def test_no_hair_still_builds_a_bald_head(self):
        img, face, mask = synthetic(hair=False)
        sp = sprite.build(img, face, mask, sprite.Options())
        self.assertIn("no hair found", sp.notes)
        self.assertNotIn("hair", sp.colors)

    def test_no_mask_still_builds_from_the_landmarks(self):
        sp = sprite.build(self.img, self.face, None, sprite.Options())
        self.assertEqual(sp.image.size, (32, 32))

    def test_too_few_landmarks_is_a_sentence_not_a_crash(self):
        with self.assertRaises(sprite.SpriteProblem):
            sprite.build(
                self.img, {"box": [0.1, 0.1, 0.3, 0.3], "points": {"contour": [[0.1, 0.1]]}}, None
            )

    def test_exports_are_nearest_neighbour_zooms(self):
        sp = self.build()
        four = Image.open(io.BytesIO(sp.png(4)))
        self.assertEqual(four.size, (128, 128))
        self.assertEqual(four.getpixel((5, 5)), sp.image.getpixel((1, 1)))


class FacialHairTests(unittest.TestCase):
    def test_it_measures_full_beard_goatee_mustache_and_none(self):
        for kind in ("full", "goatee", "mustache", None):
            img, face, mask = synthetic(beard=kind)
            sp = sprite.build(img, face, mask, sprite.Options())
            self.assertEqual(sp.facial_hair, kind or "none", kind)
            self.assertTrue(sp.facial_hair_measured)

    def test_a_beard_is_drawn_as_a_hair_coloured_mass_below_the_nose(self):
        img, face, mask = synthetic(beard="full")
        a = np.asarray(sprite.build(img, face, mask, sprite.Options()).image)
        b = np.asarray(sprite.build(img, face, mask, sprite.Options(beard="none")).image)
        hair = sprite.map_colors(SKIN, HAIR_RGB, "gameboy")["hair"]
        low = slice(18, 25)  # the lower face rows of the 16 px head (canvas offset 8)
        count = lambda arr: int((arr[low][..., :3] == hair).all(axis=2).sum())  # noqa: E731
        self.assertGreater(count(a), count(b) + 4)

    def test_the_user_can_choose_it(self):
        img, face, mask = synthetic(beard="full")
        for words, kind in (
            ("goatee", "goatee"),
            ("Moustache", "mustache"),
            ("clean shaven", "none"),
            ("stubble", "stubble"),
            ("full beard", "full"),
        ):
            sp = sprite.build(img, face, mask, sprite.Options(beard=words))
            self.assertEqual((sp.facial_hair, sp.facial_hair_measured), (kind, False), words)
        self.assertEqual(sprite.beard_name("whatever"), "")
        self.assertEqual(sprite.beard_name(""), "")

    def test_stubble_uses_the_hair_colour_when_it_differs_from_the_skin(self):
        img, face, mask = synthetic()
        sp = sprite.build(img, face, mask, sprite.Options(beard="stubble", palette="pico8"))
        if sp.colors["hair"] not in (sp.colors["line"], sp.colors["skin"]):
            self.assertEqual(sp.colors.get("shade"), sp.colors["hair"])
        else:  # black hair is the outline colour: stubble is then a darker skin tone, never the outline
            self.assertNotIn(sp.colors.get("shade"), (sp.colors["line"], sp.colors["skin"]))

    def test_a_dim_brown_skin_maps_to_a_brown_not_a_grey(self):
        dark_brown = (124, 84, 67)  # measured on a real selfie: pico8 used to give grey 5F574F
        pal = lambda n: [pixel._rgb(c) for c in pixel.PALETTES[n]]  # noqa: E731
        self.assertEqual(sprite._hex(sprite._skin_shade(dark_brown, pal("pico8"))), "AB5236")
        self.assertEqual(sprite._hex(sprite._skin_shade(dark_brown, pal("nes"))), "503000")
        self.assertEqual(sprite._hex(sprite._skin_shade((240, 200, 170), pal("pico8"))), "FFCCAA")

    def test_the_chibi_face_is_one_clean_window_with_the_eyes_clear_of_the_hair(self):
        img, face, mask = synthetic(beard="full")
        h = sprite._Head(img, face, mask, sprite.Options(chibi=True).clean())
        h.cells()
        h.features(sprite.map_colors(h.skin_rgb, h.hair_rgb, "gameboy"))
        g = h.grid
        eyes = list(zip(*np.nonzero(g == sprite.LINE)))
        inner = [(y, x) for y, x in eyes if 3 <= y <= 11 and 3 <= x <= 12]  # not the outline
        self.assertTrue(inner)
        for (
            y,
            x,
        ) in (
            inner
        ):  # every eye pixel has skin or another eye pixel on its left and right, never hair
            for nx in (x - 1, x + 1):
                self.assertIn(int(g[y, nx]), (sprite.SKIN, sprite.LINE), (y, x))
        row = g[6]  # a row through the face window: one skin run, not a cross
        runs = np.diff(np.concatenate([[0], (row == sprite.SKIN).astype(int), [0]]))
        self.assertEqual(int((runs == 1).sum()), 1)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.mine = self.root / "Pictures"
        self.mine.mkdir()
        self.img, self.face, self.mask = synthetic()
        self.img.save(self.mine / "me.png")
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.root / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        charts.set_panel(self.panel, self.files, lambda: 7)
        self.addCleanup(charts.set_panel, None, None)
        for target, value in (
            (art_tool.faces, ("landmarks", (self.face, self.mask))),
            (art_tool.faces, ("detect", [(140 / W, 80 / H, 120 / W, 155 / H)])),
        ):
            p = mock.patch.object(target, value[0], return_value=value[1])
            p.start()
            self.addCleanup(p.stop)

    def run_tool(self, **kw):
        cfg = mock.patch.object(
            source.config, "load_config", return_value={"art": {"folders": [str(self.mine)]}}
        )
        with cfg:
            return art_tool.image_style("me", style="sprite", **kw)

    def test_the_card_has_photo_sprite_and_pixel_art_filter_side_by_side(self):
        said = self.run_tool()
        card = self.sent[-1]
        self.assertEqual(pn.validate(card), [])
        self.assertEqual((card["type"], card["kind"]), ("panel.art", "sprite"))
        for k in ("before", "after", "compare", "export", "small"):
            self.assertTrue(card[k].endswith(".png"), k)
        self.assertIn("16 by 16 pixel head", said)
        self.assertIn("not generated", said)
        self.assertEqual(
            [p for p in (self.root / "p").iterdir() if p.is_file()], []
        )  # nothing on disk yet

    def test_options_reach_the_sprite(self):
        said = self.run_tool(head=24, palette="nes", chibi=True, big_eyes=True, nose=True)
        card = self.sent[-1]
        self.assertIn("24 by 24", said)
        self.assertIn("Palette: nes", card["settings"])
        self.assertIn("Proportions: chibi", card["settings"])

    def test_save_this_card_writes_1x_4x_8x_and_a_note(self):
        self.run_tool()
        saved = self.root / "Julius Saved"
        self.assertIn("Saved to", pe.save_card(self.panel, self.files, str(saved)))
        (folder,) = list(saved.iterdir())
        self.assertEqual(
            sorted(p.name for p in folder.iterdir()),
            ["card.md", "sprite-1x.png", "sprite-4x.png", "sprite-8x.png"],
        )
        one, four, eight = (Image.open(folder / f"sprite-{n}.png") for n in ("1x", "4x", "8x"))
        self.assertEqual(
            (four.size, eight.size),
            ((one.width * 4, one.height * 4), (one.width * 8, one.height * 8)),
        )
        self.assertEqual(one.mode, "RGBA")

    def test_save_that_sprite_from_earlier_finds_it_behind_newer_cards(self):
        self.run_tool()
        sprite_card = self.sent[-1]["panel_id"]
        with mock.patch.object(
            source.config, "load_config", return_value={"art": {"folders": [str(self.mine)]}}
        ):
            art_tool.pixel_art("me", size=16)
            art_tool.filter_style("me", "pencil")
        self.assertNotEqual(self.sent[-1]["panel_id"], sprite_card)
        saved = self.root / "Julius Saved"
        out = pe.handle(self.panel, self.files, "save that sprite from earlier", str(saved))
        self.assertIn("Saved to", out)
        (folder,) = list(saved.iterdir())
        self.assertIn("sprite-8x.png", [p.name for p in folder.iterdir()])
        self.assertIn(
            "Saved to", pe.handle(self.panel, self.files, "Save the pixel art.", str(saved))
        )
        self.assertIn(
            "Saved to", pe.handle(self.panel, self.files, "save the pencil sketch", str(saved))
        )
        names = {p.name for f in saved.iterdir() for p in f.iterdir()}
        self.assertTrue({"sprite-1x.png", "pixel-art.png", "restyled.png"} <= names, names)

    def test_saving_something_that_is_not_there_says_so(self):
        self.assertIn(
            "don't have a sprite",
            pe.handle(self.panel, self.files, "save that sprite", str(self.root / "x")),
        )

    def test_the_last_twenty_cards_keep_their_pictures_and_stay_in_the_hub(self):
        from julius.ui_bridge.panel import HISTORY

        self.assertEqual(HISTORY, 20)
        refs = [self.files.put_memory(b"x" * 100, "png") for _ in range(120)]
        self.assertTrue(
            all(self.files.bytes_of(r) for r in refs)
        )  # 20 cards x 5 pictures, plus room
        self.assertGreaterEqual(pf.MEMORY_TTL_S, 12 * 3600)

    def test_no_face_is_a_plain_sentence(self):
        with mock.patch.object(art_tool.faces, "landmarks", return_value=(None, None)):
            said = self.run_tool()
        self.assertIn("couldn't find a face", said)
        self.assertEqual(self.sent, [])

    def test_the_styles_route_sprite_words_to_sprite_and_pixel_words_to_pixel_art(self):
        for text, want in (
            ("sprite", "sprite"),
            ("make me a game sprite", "sprite"),
            ("Game Boy character", "sprite"),
            ("retro game character", "sprite"),
            ("avatar", "sprite"),
            ("pixel art", "pixel_art"),
            ("pixelate", "pixel_art"),
            ("pixel art sprite", "pixel_art"),
            ("8-bit", "pixel_art"),
            ("game boy", "pixel_art"),
            ("retro", "pixel_art"),
            ("cartoon", "cel_shade"),
            ("pencil sketch", "pencil"),
        ):
            self.assertEqual(art_tool.style_name(text), want, text)

    def test_an_8_bit_sprite_request_is_no_longer_a_pixel_art_shortcut(self):
        from julius.art import commands

        self.assertIsNone(commands.parse("Can you make profile.png look like an 8-bit sprite?"))
        self.assertIsNotNone(commands.parse("Make sunset.jpg into pixel art"))


class FacesTests(unittest.TestCase):
    def test_landmarks_parses_the_helper_and_cleans_up_the_mask_file(self):
        im = Image.new("RGB", (200, 150), (10, 10, 10))
        seen = {}

        def runner(cmd, **kw):
            seen["cmd"] = cmd
            Image.new("L", (40, 30), 255).save(cmd[-1], "PNG")
            out = '{"w":200,"h":150,"mask":true,"faces":[{"box":[0.3,0.2,0.3,0.4],"roll":0,"points":{"contour":[[0.3,0.5]]}}]}\n'
            return mock.Mock(stdout=out.encode())

        with mock.patch("julius.agents.vision.build", return_value=Path("/x/visiond")):
            face, mask = faces.landmarks(im, runner)
        self.assertEqual(face["box"], [0.3, 0.2, 0.3, 0.4])
        self.assertEqual(mask.size, (40, 30))
        self.assertEqual(seen["cmd"][1], "landmarks")
        self.assertFalse(Path(seen["cmd"][-1]).exists())

    def test_no_face_or_a_broken_helper_gives_none(self):
        im = Image.new("RGB", (50, 50))
        none = mock.Mock(stdout=b'{"w":50,"h":50,"faces":[],"mask":false}\n')
        with mock.patch("julius.agents.vision.build", return_value=Path("/x/visiond")):
            self.assertEqual(faces.landmarks(im, lambda *a, **k: none), (None, None))
            self.assertEqual(faces.landmarks(im, mock.Mock(side_effect=OSError)), (None, None))
        with mock.patch("julius.agents.vision.build", return_value=Path("/x/visiond")):
            tiny = mock.Mock(
                stdout=b'{"w":50,"h":50,"faces":[{"box":[0.1,0.1,0.02,0.02],"points":{"contour":[[0,0]]}}],"mask":false}\n'
            )
            self.assertEqual(faces.landmarks(im, lambda *a, **k: tiny), (None, None))


class PanelTests(unittest.TestCase):
    def test_the_compare_picture_is_optional_and_must_be_a_panel_file(self):
        hub = ev.EventHub()
        sent = []
        hub._send = lambda m: sent.append(m)
        panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        ref = "/p/aaaaaaaa.png"
        panel.art(
            {
                "title": "t",
                "kind": "sprite",
                "before": ref,
                "after": ref,
                "export": ref,
                "small": ref,
            }
        )
        self.assertEqual(pn.validate(sent[-1]), [])
        self.assertEqual(sent[-1]["compare"], "")
        bad = dict(sent[-1], compare="https://evil.example/x.png")
        self.assertTrue(any("compare" in e for e in pn.validate(bad)))


if __name__ == "__main__":
    unittest.main()
