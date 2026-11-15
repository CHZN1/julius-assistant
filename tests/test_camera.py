"""Stage 5c-art camera: "take a photo / selfie" opens the orb's camera window; the photo comes back over the UI channel, is held in memory, works like a dropped image,
and is saved only on request."""

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
import julius.ui_bridge.uichannel as uc
from julius.art import camera, source
from julius.art import tool as art_tool
from tests.test_uichannel import Base


def jpeg(color=(200, 150, 120), size=(64, 48)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


class ParseTests(unittest.TestCase):
    def test_take_a_photo_requests(self):
        for text, then in [
            ("take a photo", ""),
            ("Take a selfie.", ""),
            ("hey julius, take a selfie", ""),
            ("can you take a picture", ""),
            ("snap a selfie", ""),
            ("use the camera to take my picture", ""),
            ("take a selfie and make it pixel art", "make it pixel art"),
            (
                "take a photo then turn it into pixel art, gameboy palette",
                "turn it into pixel art, gameboy palette",
            ),
            ("Take a selfie and make it a pencil sketch.", "make it a pencil sketch"),
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


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.root / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        self.addCleanup(self.tmp.cleanup)
        for f in (source.forget_held, camera.cancel_pending):
            f()
            self.addCleanup(f)
        self.addCleanup(source.set_drop_source, None)
        source.set_drop_source(None)

    def opens(self):
        return [m for m in self.sent if m["type"] == "panel.camera_open"]

    def test_the_request_asks_the_orb_to_open_the_window_and_start_the_countdown(self):
        said = camera.request({}, self.panel)
        (m,) = self.opens()
        self.assertEqual((m["autostart"], m["auto_use_s"]), (True, 0))
        self.assertEqual(pn.validate(m), [])
        self.assertIn("Counting down", said)

    def test_a_follow_up_makes_the_orb_use_the_photo_by_itself_after_five_seconds(self):
        camera.request({}, self.panel, "make it pixel art")
        self.assertEqual(self.opens()[0]["auto_use_s"], camera.AUTO_USE_S)

    def test_switched_off_or_no_orb(self):
        self.assertIn("switched off", camera.request({"camera": False}, self.panel))
        self.assertEqual(self.opens(), [])
        self.assertIn("needs the Julius orb", camera.request({}, None))

    def test_the_photo_is_held_in_memory_shown_as_a_still_and_the_follow_up_comes_back_once(self):
        camera.request({}, self.panel, "make it pixel art")
        then = camera.received(jpeg((10, 200, 10), (160, 120)), self.panel, self.files, 7)
        self.assertEqual(then, "make it pixel art")
        self.assertEqual(camera.take_pending(), "")  # once
        card = [m for m in self.sent if m["type"] == "panel.camera"][0]
        self.assertEqual((card["turn"], pn.validate(card)), (7, []))
        self.assertEqual([p for p in (self.root / "p").iterdir() if p.is_file()], [])
        img, label, name = source.load("dropped")
        self.assertEqual((img.size, label, name), ((160, 120), "camera photo", "camera photo"))
        for words in ("this", "my selfie", ""):
            self.assertEqual(source.load(words)[1], "camera photo")

    def test_a_photo_without_a_follow_up_returns_nothing_to_do(self):
        camera.request({}, self.panel)
        self.assertEqual(camera.received(jpeg(), self.panel, self.files, 1), "")

    def test_closing_the_window_drops_the_follow_up(self):
        from julius.runtime import inputs

        s = mock.Mock(panel_hub=self.panel)
        camera.request({}, self.panel, "make it pixel art")
        inputs.ui_camera(s, "closed")
        self.assertEqual(camera.take_pending(), "")

    def test_a_stale_follow_up_is_ignored(self):
        camera.request({}, self.panel, "make it pixel art")
        self.assertEqual(camera.take_pending(clock=lambda: 1e12), "")

    def test_permission_denied_dims_the_buttons_and_a_photo_turns_them_back_on(self):
        from julius.runtime import inputs

        s = mock.Mock(panel_hub=self.panel)
        inputs.ui_camera(s, "denied")
        st = [m for m in self.sent if m["type"] == "panel.camera_status"]
        self.assertEqual((st[-1]["enabled"], st[-1]["cause"]), (False, "permission"))
        self.assertIn("System Settings", st[-1]["reason"])
        self.assertEqual(self.panel.snapshot()["camera_status"]["cause"], "permission")
        camera.received(jpeg(), self.panel, self.files, 1)
        st = [m for m in self.sent if m["type"] == "panel.camera_status"]
        self.assertEqual(st[-1]["enabled"], True)
        self.assertTrue(all(pn.validate(m) == [] for m in self.sent))

    def test_camera_status_from_settings_and_in_the_hello_snapshot(self):
        self.panel.set_camera_status(False, "The camera is switched off.", "settings")
        self.assertEqual(
            self.panel.snapshot()["camera_status"],
            {"enabled": False, "reason": "The camera is switched off.", "cause": "settings"},
        )
        self.panel.set_camera_status(False, "x", "bogus")
        self.assertEqual(self.panel.snapshot()["camera_status"]["cause"], "")

    def test_the_ui_photo_handler_queues_the_follow_up_like_a_typed_line(self):
        from julius.runtime import inputs

        s = mock.Mock(panel_hub=self.panel, panel_files=self.files, turn=3, typed=[])
        camera.request({}, self.panel, "make it pixel art")
        res = inputs.ui_photo(s, jpeg(), auto=True)
        self.assertEqual(res, {"held": True, "then": True})
        (entry,) = s.typed
        self.assertEqual(
            (str(entry), getattr(entry, "from_ui", False)), ("make it pixel art", True)
        )

    def test_the_held_photo_follows_the_art_commands(self):
        charts.set_panel(self.panel, self.files, lambda: 7)
        self.addCleanup(charts.set_panel, None, None)
        camera.received(jpeg((120, 90, 70), (320, 240)), self.panel, self.files, 7)
        with mock.patch.object(art_tool.faces, "detect", return_value=[]):
            from julius.art import commands

            said = commands.handle("make it pixel art, gameboy palette")
        self.assertIn("Pixel art from camera photo", said)
        self.assertEqual(self.sent[-1]["type"], "panel.art")
        self.assertEqual([p for p in (self.root / "p").iterdir() if p.is_file()], [])

    def test_a_file_dropped_after_the_photo_is_the_newer_picture(self):
        drop = {"items": []}
        source.set_drop_source(lambda: drop)
        camera.received(jpeg(), self.panel, self.files, 1)
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

    def test_save_this_card_is_the_only_way_to_disk(self):
        camera.received(jpeg(), self.panel, self.files, 1)
        saved = self.root / "Julius Saved"
        said = pe.save_card(self.panel, self.files, str(saved))
        self.assertIn("Saved to", said)
        (folder,) = list(saved.iterdir())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["card.md", "photo.jpg"])


class ChannelTests(Base):
    """POST /v1/photo and POST /v1/camera."""

    def setUp(self):
        super().setUp()
        self.photos, self.events = [], []
        self.ch.handlers["photo"] = lambda data, auto=False: (
            self.photos.append((data, auto)) or {"held": True}
        )
        self.ch.handlers["camera"] = lambda e: self.events.append(e) or {}

    def post_photo(self, data, ctype="image/jpeg", path="/v1/photo", **kw):
        return self.req(path=path, raw=data, headers={"Content-Type": ctype}, **kw)

    def test_a_jpeg_is_accepted_and_the_auto_flag_comes_through(self):
        status, out = self.post_photo(jpeg())
        self.assertEqual((status, out["held"]), (200, True))
        status, _ = self.post_photo(jpeg(), path="/v1/photo?auto=1")
        self.assertEqual(status, 200)
        self.assertEqual([a for _, a in self.photos], [False, True])

    def test_wrong_type_not_a_jpeg_and_too_large_are_refused(self):
        self.assertEqual(self.post_photo(jpeg(), ctype="application/json")[0], 415)
        self.assertEqual(self.post_photo(b"not a jpeg at all")[0], 400)
        try:  # the server refuses on the header and may hang up before the whole body is sent
            self.assertEqual(self.post_photo(b"\xff\xd8" + b"0" * (uc.MAX_PHOTO + 1))[0], 413)
        except ConnectionError:
            pass
        self.assertEqual(self.photos, [])

    def test_the_token_is_still_required(self):
        self.assertEqual(self.post_photo(jpeg(), auth=False)[0], 401)

    def test_camera_events(self):
        for e in ("opened", "closed", "denied"):
            self.assertEqual(self.req(path="/v1/camera", body={"event": e})[0], 200)
        self.assertEqual(self.events, ["opened", "closed", "denied"])
        self.assertEqual(self.req(path="/v1/camera", body={"event": "explode"})[0], 400)
        self.assertEqual(self.req(path="/v1/camera", body={})[0], 400)


if __name__ == "__main__":
    unittest.main()
