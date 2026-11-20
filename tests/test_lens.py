"""Google Lens reverse image search (SerpApi) as evidence for "what is this and where can I buy one". No network: the HTTP calls are fakes."""

import io
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from julius.agents import lens, research, vision

LENS_REPLY = {
    "visual_matches": [
        {
            "title": "Kiberpasha plays Gravity Falls on a pink keyboard",
            "link": "https://www.tiktok.com/@someone/video/1",
            "source": "TikTok",
        },
        {
            "title": "Casio CTK-900 61-Key Portable Keyboard",
            "link": "https://www.guitarcenter.com/casio-ctk-900",
            "source": "Guitar Center",
        },
        {"title": "someone's reel", "link": "https://instagram.com/p/abc", "source": "Instagram"},
        {
            "title": "Model of this synth? : r/synthesizers",
            "link": "https://www.reddit.com/r/synthesizers/x",
            "source": "Reddit",
        },
        {
            "title": "Casio CTK-900 61-Key Portable Keyboard",
            "link": "https://www.guitarcenter.com/casio-ctk-900",
            "source": "Guitar Center",
        },
        {"title": "channel", "link": "https://youtube.com/@someone", "source": "YouTube"},
    ],
    "organic_results": [
        {
            "title": "Casio CTK-900 manual",
            "link": "https://support.casio.com/ctk900",
            "source": "Casio",
        }
    ],
}


class FakeHTTP:
    def __init__(self, upload=None, reply=None, fail=None):
        self.calls = []
        self.upload, self.reply, self.fail = (
            upload or {"image_id": "img123"},
            reply or LENS_REPLY,
            fail,
        )

    def _resp(self, body):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        if self.fail:
            raise self.fail
        return self._resp(self.upload)

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        return self._resp(self.reply)


class Lens(unittest.TestCase):
    def test_off_without_a_key_and_nothing_is_sent(self):
        http = FakeHTTP()
        with mock.patch.dict(os.environ, {"SERPAPI_KEY": ""}):
            self.assertFalse(lens.available())
            self.assertEqual(lens.search(b"jpg", http=http), [])
        self.assertEqual(http.calls, [])
        self.assertEqual(lens.evidence([]), "")

    def test_upload_then_one_lens_search_by_image_id(self):
        http = FakeHTTP()
        with mock.patch.dict(os.environ, {"SERPAPI_KEY": "k"}):
            found = lens.search(b"jpg", http=http)
        (m1, u1, kw1), (m2, u2, kw2) = http.calls
        self.assertEqual((m1, u1), ("post", lens.UPLOAD))
        self.assertIn("image", kw1["files"])
        self.assertEqual(kw1["data"], {"api_key": "k"})
        self.assertEqual((m2, u2), ("get", lens.SEARCH))
        self.assertEqual(
            (kw2["params"]["engine"], kw2["params"]["image_id"]), ("google_lens", "img123")
        )
        self.assertTrue(found)

    def test_social_media_is_dropped_and_repeats_are_merged(self):
        found = lens.matches(LENS_REPLY)
        self.assertEqual(
            [m["source"] for m in found], ["Guitar Center", "Reddit", "Casio"]
        )  # no TikTok, Instagram or YouTube: they name people, not products
        text = lens.evidence(found)
        self.assertIn("Casio CTK-900 61-Key Portable Keyboard", text)
        self.assertNotIn("Kiberpasha", text)

    def test_any_failure_means_no_matches_and_the_key_is_never_logged(self):
        with (
            mock.patch.dict(os.environ, {"SERPAPI_KEY": "secret-key"}),
            mock.patch.object(lens.log, "event") as ev,
        ):
            self.assertEqual(
                lens.search(b"jpg", http=FakeHTTP(fail=TimeoutError("secret-key timed out"))), []
            )
            self.assertEqual(lens.search(b"jpg", http=FakeHTTP(upload={"error": "bad"})), [])
            self.assertEqual(
                lens.search(b"jpg", http=FakeHTTP(reply={"error": "Out of searches"})), []
            )
        self.assertNotIn("secret-key", repr(ev.call_args_list))

    def test_a_big_picture_is_shrunk_under_the_upload_limit(self):
        from PIL import Image

        buf = io.BytesIO()
        Image.effect_noise((2400, 2400), 90).convert("RGB").save(buf, "JPEG", quality=95)
        self.assertGreater(len(buf.getvalue()), lens.MAX_BYTES)
        self.assertLessEqual(len(lens.fit(buf.getvalue())), lens.MAX_BYTES)
        self.assertEqual(lens.fit(b"small"), b"small")


class IdentifyWithLens(unittest.TestCase):
    def test_the_matches_are_evidence_for_identify_and_shown_on_the_card(self):
        ident = '{"name": "Casio CTK-900", "details": "61 keys, round speaker pods", "customized": "pink paint, grilles removed", "confidence": "high", "search": "Casio CTK-900"}'
        asked = []
        panel = mock.Mock()
        files = mock.Mock(put_memory=mock.Mock(return_value="/p/x.jpg"))
        brain = SimpleNamespace(config={"models": {"sonnet": "s", "haiku": "h"}})
        with (
            mock.patch.object(lens, "search", return_value=lens.matches(LENS_REPLY)),
            mock.patch.object(
                vision,
                "ask_model",
                side_effect=lambda b, t, j, q, s, system=None: (asked.append(q), (ident, 0.01))[1],
            ),
            mock.patch.object(
                research, "research_task", return_value="About $120 used on Reverb."
            ) as rt,
        ):
            said = vision.shop(
                vision.Look("image", True, "what is this and where can I buy one", shop=True),
                brain,
                b"j",
                1,
                1,
                "x",
                panel,
                files,
                2,
            )
        self.assertIn("Google Lens", asked[0])
        self.assertIn("Casio CTK-900 61-Key Portable Keyboard", asked[0])
        self.assertTrue(said.startswith("That's the Casio CTK-900."), said)
        card = panel.vision.call_args[0][0]
        self.assertIn("Checked against 3 matching web pages with Google Lens.", card["answer"])
        self.assertNotIn("Kiberpasha", card["answer"] + rt.call_args[0][0]["question"])

    def test_without_a_key_identify_is_unchanged(self):
        ident = '{"name": "Yamaha PSR-E473", "details": "", "customized": "", "confidence": "low", "search": ""}'
        with (
            mock.patch.dict(os.environ, {"SERPAPI_KEY": ""}),
            mock.patch.object(vision, "ask_model", return_value=(ident, 0.0)) as am,
        ):
            item, _ = vision.identify(SimpleNamespace(), b"j", "what is this", "image")
        self.assertEqual(am.call_args[0][3], "what is this")
        self.assertEqual(item["lens"], [])


if __name__ == "__main__":
    unittest.main()
