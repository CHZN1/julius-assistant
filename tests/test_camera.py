"""Stage 5c-art camera: "take a photo / selfie" (countdown with live preview, held in memory, used like a dropped image, saved only on request)."""

import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import julius.ui_bridge.charts as charts
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panel_emit as pe
import julius.ui_bridge.panelfiles as pf
from julius.art import camera, source
from julius.art import tool as art_tool


def jpeg(color=(200, 150, 120), size=(64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class FakeProc:
    def __init__(self, lines, code=0):
        self.stdout = iter(lines)
        self.killed = False
        self.code = code

    def wait(self, timeout=None):
        return self.code

    def poll(self):
        return self.code

    def kill(self):
        self.killed = True


def popen_with(frames=3, photo=None, tail="ok 1280x720"):
    photo = photo if photo is not None else jpeg()
    lines = [f"frame {b64(jpeg())}\n" for _ in range(frames)]
    if photo:
        lines.append(f"photo {b64(photo)}\n")
    if tail:
        lines.append(tail + "\n")
    return mock.Mock(return_value=FakeProc(lines))


class ParseTests(unittest.TestCase):
    def test_take_a_photo_requests(self):
        for text, then in [
            ("take a photo", ""),
            ("Take a selfie.", ""),
            ("hey julius, take a selfie", ""),
            ("can you take a picture", ""),
            ("snap a selfie", ""),
            ("take a selfie and make it pixel art", "and make it pixel art"),
            (
                "take a photo then turn it into pixel art, gameboy palette",
                "then turn it into pixel art, gameboy palette",
            ),
        ]:
            got = camera.parse(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got["then"], then, text)

    def test_other_requests_are_left_alone(self):
        for text in (
            "what am I holding",
            "look at me",
            "what's a selfie",
            "how do I take a photo with my phone",
            "take a picture of the sunset",
            "take a look at this",
            "turn this photo into pixel art",
            "",
        ):
            self.assertIsNone(camera.parse(text), text)

    def test_vision_no_longer_claims_take_a_picture(self):
        from julius.agents import vision

        self.assertIsNone(vision.parse("take a picture"))
        self.assertEqual(vision.parse("what am I holding").source, "camera")


class StreamTests(unittest.TestCase):
    def test_frames_go_to_the_callback_and_the_photo_comes_back(self):
        got = []
        p = jpeg((1, 2, 3))
        out = camera.stream(3, got.append, popen_with(frames=4, photo=p), Path("/x/visiond"))
        self.assertEqual(out, p)
        self.assertEqual(len(got), 4)

    def test_errors_become_sentences(self):
        for tail, text in [
            ("error camera-permission", "camera permission"),
            ("error camera-busy", "can't use the camera"),
            ("error no-frame", "couldn't take a photo"),
        ]:
            with self.assertRaises(camera.CameraProblem) as c:
                camera.stream(3, lambda b: None, popen_with(photo=b"", tail=tail), Path("/x"))
            self.assertIn(text, str(c.exception))
        with self.assertRaises(camera.CameraProblem):
            camera.stream(3, lambda b: None, mock.Mock(side_effect=OSError), Path("/x"))

    def test_a_failing_preview_does_not_lose_the_photo(self):
        def boom(b):
            raise RuntimeError

        self.assertTrue(camera.stream(3, boom, popen_with(), Path("/x")))


class TakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.root / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(source.forget_held)
        self.addCleanup(source.set_drop_source, None)
        source.forget_held()
        source.set_drop_source(None)

    def take(self, **kw):
        kw.setdefault("popen", popen_with())
        kw.setdefault("binary", Path("/x"))
        return camera.take({}, self.panel, self.files, 7, **kw)

    def test_countdown_card_updates_then_the_photo_and_nothing_on_disk(self):
        said = self.take()
        self.assertTrue(said.startswith("Got it"))
        self.assertTrue(all(pn.validate(m) == [] for m in self.sent), self.sent)
        card = self.sent[0]
        self.assertEqual((card["type"], card["turn"]), ("panel.camera", 7))
        self.assertEqual(
            (card["phase"], card["count"]), ("done", 0)
        )  # the remembered card is folded forward, so a reconnect sees the photo
        updates = [m for m in self.sent if m["type"] == "panel.update"]
        self.assertGreaterEqual(len(updates), 4)
        self.assertEqual(updates[-1]["phase"], "done")
        self.assertTrue(all(u["image"].endswith(".jpg") for u in updates))
        self.assertEqual([p for p in (self.root / "p").iterdir() if p.is_file()], [])
        self.assertIsNotNone(source.held())

    def test_previews_never_push_real_pictures_out_of_memory(self):
        keep = self.files.put_memory(b"x" * 10, "png", keep=16)
        self.take(popen=popen_with(frames=40))
        self.assertIsNotNone(self.files.bytes_of(keep))
        self.assertLessEqual(len(self.files._live), 3)

    def test_the_held_photo_works_like_a_dropped_image(self):
        self.take(popen=popen_with(photo=jpeg((10, 200, 10), (160, 120))))
        img, label, name = source.load("dropped")
        self.assertEqual((img.size, label, name), ((160, 120), "camera photo", "camera photo"))
        for words in ("this", "my selfie", ""):
            self.assertEqual(source.load(words)[1], "camera photo")

    def test_a_file_dropped_after_the_photo_is_the_newer_picture(self):
        drop = {"items": []}
        source.set_drop_source(lambda: drop)
        self.take()
        self.assertIsNotNone(source.held())
        newer = self.root / "x.png"
        Image.new("RGB", (30, 30)).save(newer)
        drop["items"] = [{"kind": "file", "path": str(newer), "name": "x.png"}]
        self.assertIsNone(source.held())
        self.assertEqual(source.load("dropped")[2], "x.png")

    def test_the_photo_is_forgotten_after_an_hour(self):
        now = [1000.0]
        source.hold(jpeg(), clock=lambda: now[0])
        self.assertIsNotNone(source.held(clock=lambda: now[0] + 100))
        self.assertIsNone(source.held(clock=lambda: now[0] + source.HELD_TTL_S + 1))

    def test_take_a_selfie_and_make_it_pixel_art_uses_the_held_photo(self):
        charts.set_panel(self.panel, self.files, lambda: 7)
        self.addCleanup(charts.set_panel, None, None)
        self.take(popen=popen_with(photo=jpeg((120, 90, 70), (320, 240))))
        with mock.patch.object(art_tool.faces, "detect", return_value=[]):
            from julius.art import commands

            said = commands.handle("and make it pixel art, gameboy palette")
        self.assertIn("Pixel art from camera photo", said)
        self.assertEqual(self.sent[-1]["type"], "panel.art")
        self.assertEqual([p for p in (self.root / "p").iterdir() if p.is_file()], [])

    def test_failure_marks_the_card_and_says_why(self):
        said = self.take(popen=popen_with(photo=b"", tail="error camera-permission"))
        self.assertIn("camera permission", said)
        self.assertEqual(self.sent[-1]["phase"], "failed")
        self.assertIsNone(source.held())

    def test_switched_off_in_settings(self):
        said = camera.take({"camera": False}, self.panel, self.files, 1, popen=mock.Mock())
        self.assertIn("switched off", said)
        self.assertEqual(self.sent, [])

    def test_save_this_card_is_the_only_way_to_disk(self):
        self.take()
        saved = self.root / "Julius Saved"
        said = pe.save_card(self.panel, self.files, str(saved))
        self.assertIn("Saved to", said)
        (folder,) = list(saved.iterdir())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["card.md", "photo.jpg"])

    def test_nothing_to_save_while_the_countdown_runs(self):
        card = self.panel.camera({"phase": "countdown", "count": 3}, turn=1)
        self.assertIsNotNone(card)
        self.assertIn(
            "no finished photo", pe.save_card(self.panel, self.files, str(self.root / "s"))
        )


if __name__ == "__main__":
    unittest.main()
