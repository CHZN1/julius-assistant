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
        {
            "title": "Stream someone music",
            "link": "https://soundcloud.com/someone",
            "source": "SoundCloud",
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
    "ai_overview": {"page_token": "tok"},
}
OVERVIEW = {
    "ai_overview": {
        "text_blocks": [
            {
                "type": "paragraph",
                "snippet": "This appears to be a Casio CTK-900 that has been custom-painted pink with its speaker grilles removed.",
            },
            {"type": "heading", "snippet": "Key Visual Details"},
            {
                "type": "list",
                "list": [
                    {"snippet": "Layout: 61 keys and a blue backlit display."},
                    {"snippet": "Seen in street videos from creator @someone39."},
                ],
            },
            {"type": "paragraph", "snippet": "Are you looking for the video?"},
        ]
    }
}
NONE = {"matches": [], "overview": ""}


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
        return self._resp(
            OVERVIEW if kw["params"]["engine"] == "google_ai_overview" else self.reply
        )


class Lens(unittest.TestCase):
    def test_off_without_a_key_and_nothing_is_sent(self):
        http = FakeHTTP()
        with mock.patch.dict(os.environ, {"SERPAPI_KEY": ""}):
            self.assertFalse(lens.available())
            self.assertEqual(lens.search(b"jpg", http=http), NONE)
        self.assertEqual(http.calls, [])
        self.assertEqual(lens.evidence(NONE), "")

    def test_upload_then_a_lens_search_then_its_ai_overview(self):
        http = FakeHTTP()
        with mock.patch.dict(os.environ, {"SERPAPI_KEY": "k"}):
            found = lens.search(b"jpg", http=http)
        (m1, u1, kw1), (m2, u2, kw2), (m3, _, kw3) = http.calls
        self.assertEqual((m1, u1), ("post", lens.UPLOAD))
        self.assertIn("image", kw1["files"])
        self.assertEqual(kw1["data"], {"api_key": "k"})
        self.assertEqual((m2, u2), ("get", lens.SEARCH))
        self.assertEqual(
            (kw2["params"]["engine"], kw2["params"]["image_id"]), ("google_lens", "img123")
        )
        self.assertEqual(
            (m3, kw3["params"]["engine"], kw3["params"]["page_token"]),
            ("get", "google_ai_overview", "tok"),
        )
        self.assertTrue(found["matches"])
        self.assertIn("Casio CTK-900 that has been custom-painted pink", found["overview"])

    def test_the_overview_leaves_out_people_social_media_and_questions(self):
        text = lens.overview_text(OVERVIEW)
        self.assertIn("61 keys and a blue backlit display", text)
        self.assertNotIn("@someone", text)
        self.assertNotIn("Key Visual Details", text)
        self.assertNotIn("Are you looking", text)

    def test_social_media_is_dropped_and_repeats_are_merged(self):
        found = lens.matches(LENS_REPLY)
        self.assertEqual(
            [m["source"] for m in found], ["Guitar Center", "Reddit", "Casio"]
        )  # they name people, not products
        text = lens.evidence({"matches": found, "overview": ""})
        self.assertIn("Casio CTK-900 61-Key Portable Keyboard", text)
        self.assertNotIn("Kiberpasha", text)
        self.assertNotIn("someone", text)

    def test_only_the_item_is_cropped_out(self):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (1000, 800), (200, 0, 120)).save(buf, "JPEG")
        out = Image.open(io.BytesIO(lens.crop(buf.getvalue(), [0.0, 0.5, 0.8, 1.0])))
        self.assertEqual(
            out.size, (848, 424)
        )  # the box plus a 6% margin each side, kept inside the picture
        self.assertEqual(lens.crop(b"j", None), b"j")
        self.assertEqual(lens.crop(b"j", [0, 0, 1, 1]), b"j")  # the whole picture: nothing to cut

    def test_any_failure_means_no_matches_and_the_key_is_never_logged(self):
        with (
            mock.patch.dict(os.environ, {"SERPAPI_KEY": "secret-key"}),
            mock.patch.object(lens.log, "event") as ev,
        ):
            self.assertEqual(
                lens.search(b"jpg", http=FakeHTTP(fail=TimeoutError("secret-key timed out"))), NONE
            )
            self.assertEqual(lens.search(b"jpg", http=FakeHTTP(upload={"error": "bad"})), NONE)
            self.assertEqual(
                lens.search(b"jpg", http=FakeHTTP(reply={"error": "Out of searches"})), NONE
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
    def test_locate_crop_search_then_name_it_from_the_evidence(self):
        first = '{"name": "Yamaha PSR-E373", "details": "61 keys", "customized": "pink paint", "confidence": "low", "search": "", "box": [0, 0.5, 0.8, 1]}'
        ident = '{"name": "Casio CTK-900", "details": "61 keys, round speaker pods", "customized": "pink paint, grilles removed", "confidence": "high", "search": "Casio CTK-900"}'
        asked, replies = [], iter([first, ident])
        panel = mock.Mock()
        files = mock.Mock(put_memory=mock.Mock(return_value="/p/x.jpg"))
        brain = SimpleNamespace(config={"models": {"sonnet": "s", "haiku": "h"}})
        found = {"matches": lens.matches(LENS_REPLY), "overview": lens.overview_text(OVERVIEW)}
        with (
            mock.patch.dict(os.environ, {"SERPAPI_KEY": "k"}),
            mock.patch.object(lens, "crop", return_value=b"item") as crop,
            mock.patch.object(lens, "search", return_value=found) as search,
            mock.patch.object(
                vision,
                "ask_model",
                side_effect=lambda b, t, j, q, s, system=None: (
                    asked.append((j, q)),
                    (next(replies), 0.01),
                )[1],
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
        self.assertEqual(crop.call_args[0][1], [0, 0.5, 0.8, 1])  # the first look's box
        search.assert_called_once_with(
            b"item"
        )  # Lens sees only the item, never the people around it
        self.assertEqual(asked[0], (b"j", "what is this and where can I buy one"))
        self.assertEqual(asked[1][0], b"item")
        self.assertIn("Google's summary: This appears to be a Casio CTK-900", asked[1][1])
        self.assertIn("Never name or describe a person", asked[1][1])
        self.assertTrue(said.startswith("That's the Casio CTK-900."), said)
        card = panel.vision.call_args[0][0]
        self.assertIn("Checked against 3 matching web pages with Google Lens.", card["answer"])
        self.assertNotIn("Kiberpasha", card["answer"] + rt.call_args[0][0]["question"])

    def test_without_a_key_identify_is_one_look_as_before(self):
        ident = '{"name": "Yamaha PSR-E473", "details": "", "customized": "", "confidence": "low", "search": "", "box": [0, 0, 1, 1]}'
        with (
            mock.patch.dict(os.environ, {"SERPAPI_KEY": ""}),
            mock.patch.object(vision, "ask_model", return_value=(ident, 0.0)) as am,
            mock.patch.object(lens, "search") as search,
        ):
            item, _ = vision.identify(SimpleNamespace(), b"j", "what is this", "image")
        am.assert_called_once()
        search.assert_not_called()
        self.assertEqual(am.call_args[0][3], "what is this")
        self.assertEqual((item["name"], item["lens"]), ("Yamaha PSR-E473", []))
        self.assertNotIn("box", item)


if __name__ == "__main__":
    unittest.main()
