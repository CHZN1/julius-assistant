"""Vision: explicit-request matching, the camera/screen guards, image prep, cost accounting, the in-memory picture store and "nothing on disk unless save this card"."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from julius.agents import vision as v
from julius.ui_bridge import events as ev
from julius.ui_bridge import panel as pn
from julius.ui_bridge import panel_emit as pe
from julius.ui_bridge import panelfiles as pf


def jpeg(w=3000, h=2000, colour=(200, 60, 60)):
    out = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(out, "JPEG")
    return out.getvalue()


class Tracker:
    def __init__(self, capped=False):
        self.rows, self.capped = [], capped

    def record(self, tier, usage, note=""):
        self.rows.append((tier, note))

    def cap_reached(self):
        return self.capped


class Client:
    def __init__(self, text="A red mug."):
        self.calls, self.text = [], text
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.text)],
            usage=SimpleNamespace(input_tokens=2200, output_tokens=40),
        )


class Brain:
    def __init__(self, capped=False, text="A red mug."):
        self.client = Client(text)
        self.tracker = Tracker(capped)
        self.auth_bad = False
        self.config = {"models": {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"}}

    def slow_client(self):
        return self.client


class Panel:
    def __init__(self):
        self.cards = []

    def vision(self, card, turn=None):
        self.cards.append(card)
        return "p1"


def fake_runner(status=None, camera="ok 1280x720", shot=True, log=None):
    """Stands in for the helper binary and screencapture."""
    status = status or {
        "locked": False,
        "secure_input": False,
        "window": {"id": 77, "owner": "Safari", "title": "x", "w": 900, "h": 700},
    }

    def run(cmd, **kw):
        if log is not None:
            log.append(list(cmd))
        if "status" in cmd:
            return SimpleNamespace(returncode=0, stdout=json.dumps(status) + "\n", stderr="")
        if "camera" in cmd:
            if camera.startswith("ok"):
                Path(cmd[-1]).write_bytes(jpeg(1280, 720))
            return SimpleNamespace(
                returncode=0 if camera.startswith("ok") else 3, stdout=camera + "\n", stderr=""
            )
        if shot:  # screencapture
            Path(cmd[-1]).write_bytes(jpeg(1600, 1000) + b"\0" * 1200)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run


class Parse(unittest.TestCase):
    def test_explicit_requests_only(self):
        cases = {
            "what am I holding": "camera",
            "What's this in my hand?": "camera",
            "look at me": "camera",
            "use the camera and tell me what you see": "camera",
            "what's on my screen": "window",
            "look at my screen": "window",
            "take a screenshot and read it": "window",
            "what's on the whole screen": "screen",
            "read my entire screen": "screen",
            "look at this image": "image",
            "describe this picture": "image",
        }
        for text, source in cases.items():
            got = v.parse(text)
            self.assertIsNotNone(got, text)
            self.assertEqual(got.source, source, text)

    def test_ordinary_talk_never_triggers(self):
        for text in (
            "what's the weather",
            "close the window",
            "how do I hold a guitar",
            "tell me about the camera industry",
            "what's the score",
            "open the screen saver settings",
            "read this",
            "look closely",
        ):
            self.assertIsNone(v.parse(text), text)

    def test_look_closely_picks_sonnet_and_needs_a_source(self):
        self.assertTrue(v.parse("look closely at my screen").closely)
        self.assertTrue(v.parse("read this", has_dropped=True).closely)
        self.assertEqual(v.parse("read this", has_dropped=True).source, "image")
        self.assertFalse(v.parse("what's on my screen").closely)


def make_hub():
    ev_hub = ev.EventHub()
    ev_hub._send = lambda m: None
    return pn.PanelHub(ev_hub, base="http://127.0.0.1:1/tok")


class Looking(unittest.TestCase):
    hub = staticmethod(make_hub)

    def go(self, text, brain=None, runner=None, drop=None, cfg=None, panel=None, files=None):
        brain = brain or Brain()
        panel = panel if panel is not None else Panel()
        files = files if files is not None else pf.PanelFiles(Path(tempfile.mkdtemp()))
        out = v.look(
            v.parse(text, has_dropped=drop is not None),
            brain=brain,
            panel=panel,
            files=files,
            turn=3,
            drop_stage=drop,
            cfg=cfg,
            runner=runner or fake_runner(),
            binary=Path("/bin/true"),
        )
        return out, brain, panel, files

    def test_camera_look_answers_shows_a_card_and_prices_it(self):
        out, brain, panel, files = self.go("what am I holding")
        self.assertEqual(out, "A red mug.")
        call = brain.client.calls[0]
        self.assertEqual(call["model"], "claude-haiku-5-5")
        block = call["messages"][0]["content"][0]
        self.assertEqual(block["source"]["media_type"], "image/jpeg")
        self.assertEqual(brain.tracker.rows, [("haiku", "vision")])
        card = panel.cards[0]
        self.assertEqual(card["source"], "camera")
        self.assertGreater(card["cost_usd"], 0)
        self.assertGreater(card["est_cost_usd"], 0)
        self.assertTrue(card["image"].startswith("/p/"))

    def test_picture_is_shrunk_to_1568_on_the_long_edge(self):
        data, w, h = v.prepare(jpeg(4000, 3000))
        self.assertEqual((w, h), (1568, 1176))
        self.assertEqual(Image.open(io.BytesIO(data)).size, (1568, 1176))
        _, w, h = v.prepare(jpeg(800, 600))
        self.assertEqual((w, h), (800, 600))

    def test_look_closely_uses_sonnet(self):
        _, brain, _, _ = self.go("look closely at my screen")
        self.assertEqual(brain.client.calls[0]["model"], "claude-sonnet-5-5")
        self.assertEqual(brain.tracker.rows, [("sonnet", "vision")])

    def test_window_look_targets_the_frontmost_non_julius_window(self):
        log = []
        _, _, panel, _ = self.go("what's on my screen", runner=fake_runner(log=log))
        status = next(c for c in log if "status" in c)
        self.assertIn("Julius Orb", status[-1])
        shot = next(c for c in log if "screencapture" in c[0])
        self.assertIn("-l77", shot)
        self.assertNotIn("-m", shot)
        self.assertEqual(panel.cards[0]["source"], "window")

    def test_whole_screen_uses_the_main_display(self):
        log = []
        self.go("look at the whole screen", runner=fake_runner(log=log))
        shot = next(c for c in log if "screencapture" in c[0])
        self.assertIn("-m", shot)

    def test_locked_screen_and_secure_field_are_never_captured(self):
        for status, word in (
            ({"locked": True, "secure_input": False, "window": None}, "locked"),
            ({"locked": False, "secure_input": True, "window": {"id": 1}}, "password"),
        ):
            log = []
            out, brain, panel, _ = self.go(
                "what's on my screen", runner=fake_runner(status, log=log)
            )
            self.assertIn(word, out)
            self.assertFalse([c for c in log if "screencapture" in c[0]])
            self.assertEqual(brain.client.calls, [])
            self.assertEqual(panel.cards, [])

    def test_camera_permission_denied_is_explained_and_nothing_is_sent(self):
        out, brain, _, _ = self.go(
            "what am I holding", runner=fake_runner(camera="error camera-permission")
        )
        self.assertIn("camera permission", out)
        self.assertEqual(brain.client.calls, [])

    def test_spending_cap_and_settings_switches(self):
        out, brain, _, _ = self.go("what am I holding", brain=Brain(capped=True))
        self.assertIn("spending limit", out)
        self.assertEqual(brain.client.calls, [])
        out, brain, _, _ = self.go("what am I holding", cfg={"camera": False})
        self.assertIn("switched off", out)
        out, brain, _, _ = self.go("what's on my screen", cfg={"screen": False})
        self.assertIn("switched off", out)

    def test_dropped_image_is_looked_at_and_non_images_are_refused(self):
        d = Path(tempfile.mkdtemp())
        (d / "cat.jpg").write_bytes(jpeg(900, 600))
        (d / "notes.txt").write_text("hi")

        def stage(*names):
            return SimpleNamespace(
                pending=lambda: {
                    "items": [{"path": str(d / n), "name": n, "kind": "file"} for n in names]
                }
            )

        out, brain, panel, _ = self.go("look at this image", drop=stage("cat.jpg"))
        self.assertEqual(out, "A red mug.")
        self.assertEqual(panel.cards[0]["source"], "image")
        out, brain, _, _ = self.go("look at this image", drop=stage("notes.txt"))
        self.assertIn("None of the files", out)
        self.assertEqual(brain.client.calls, [])
        out, _, _, _ = self.go("look at this image", drop=SimpleNamespace(pending=lambda: None))
        self.assertIn("Drop an image", out)

    def test_nothing_is_written_to_disk_until_save_this_card(self):
        root = Path(tempfile.mkdtemp())
        files = pf.PanelFiles(root)
        hub = self.hub()
        out, _, panel, files = self.go("what am I holding", files=files, panel=hub)
        self.assertEqual(list(root.iterdir()), [])  # the picture is in memory only
        card = hub.lookup()
        self.assertEqual(card["type"], "panel.vision")
        got = files.bytes_of(card["image"])
        self.assertIsNotNone(got)
        self.assertEqual(got[1], "jpg")
        saved = Path(tempfile.mkdtemp())
        said = pe.save_card(hub, files, str(saved))
        self.assertIn("Saved", said)
        folder = next(saved.iterdir())
        self.assertTrue((folder / "look.jpg").exists())
        self.assertIn("A red mug.", (folder / "card.md").read_text())

    def test_memory_store_is_bounded_and_expires(self):
        clock = [1000.0]
        files = pf.PanelFiles(Path(tempfile.mkdtemp()), clock=lambda: clock[0])
        refs = [files.put_memory(b"x" * 10, "jpg", keep=3) for _ in range(5)]
        self.assertIsNone(files.bytes_of(refs[0]))
        self.assertIsNotNone(files.bytes_of(refs[-1]))
        clock[0] += 4000
        files.put_memory(b"y", "jpg")
        self.assertIsNone(files.bytes_of(refs[-1]))

    def test_card_validates_and_the_server_serves_memory_pictures(self):
        hub = self.hub()
        files = pf.PanelFiles(Path(tempfile.mkdtemp()))
        hub.vision(
            {
                "source": "camera",
                "question": "q",
                "answer": "a",
                "image": files.put_memory(jpeg(100, 100), "jpg"),
            },
            turn=1,
        )
        card = hub.lookup()
        self.assertEqual(pn.validate(card), [])
        name = card["image"].rsplit("/", 1)[1]
        data, ext = files.read_ref(files.token, name)
        self.assertEqual(ext, "jpg")
        self.assertIsNone(files.read_ref("wrong-token", name))


if __name__ == "__main__":
    unittest.main()
