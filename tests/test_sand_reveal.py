"""The sprite card's sand reveal: small photo and sprite grids on the card (the page animates one into the other)."""

import io
import unittest

from PIL import Image

from julius.art import tool
from julius.ui_bridge import panel as pn


def _png(im: Image.Image) -> bytes:
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


class RevealTests(unittest.TestCase):
    def test_grids_match_the_sprite(self):
        spr = Image.new("RGBA", (4, 3), (0, 0, 0, 0))
        spr.putpixel((1, 1), (255, 0, 16, 255))
        photo = Image.new("RGB", (40, 30), (10, 20, 30))
        r = tool.reveal(photo, _png(spr))
        self.assertEqual(r[:2], [4, 3])
        self.assertTrue(pn.reveal_ok(r))
        self.assertEqual(
            r[3][(1 * 4 + 1) * 6 : (1 * 4 + 2) * 6], "FF0010"
        )  # the sprite's pixel, exactly
        self.assertEqual(r[3].count("------"), 11)  # the rest is clear
        self.assertIn("0A141E", r[2])  # the photo's colour

    def test_no_photo_is_all_clear(self):
        spr = Image.new("RGBA", (2, 2), (1, 2, 3, 255))
        r = tool.reveal(None, _png(spr))
        self.assertEqual(r[2], "------" * 4)
        self.assertTrue(pn.reveal_ok(r))

    def test_too_big_or_broken_gives_none(self):
        self.assertIsNone(tool.reveal(None, _png(Image.new("RGBA", (65, 8)))))
        self.assertIsNone(tool.reveal(None, b"not a png"))

    def test_validator(self):
        self.assertFalse(pn.reveal_ok([2, 1, "FFFFFF", "FFFFFF------"]))  # wrong length
        self.assertFalse(pn.reveal_ok([2, 1, "<scrip", "FFFFFF------"]))
        self.assertFalse(pn.reveal_ok([0, 1, "", ""]))
        self.assertFalse(pn.reveal_ok("FFFFFF"))
        self.assertTrue(pn.reveal_ok([1, 1, "------", "ABCDEF"]))


if __name__ == "__main__":
    unittest.main()


class OtherConversationTests(unittest.TestCase):
    def test_a_card_from_another_conversation_is_not_updated_in_place(self):
        from julius.ui_bridge import events as ev

        hub = pn.PanelHub(ev.EventHub(), base="http://127.0.0.1:1")
        cur = {"id": 1}
        hub.conv_fn = lambda: cur["id"]
        ref = "/p/" + "a" * 32 + ".png"
        pid = hub.art(
            {
                "title": "Sprite",
                "kind": "sprite",
                "before": ref,
                "after": ref,
                "export": ref,
                "small": ref,
                "sprite_id": "ab12",
            }
        )
        self.assertTrue(pid)
        self.assertTrue(hub.update_art(pid, {"after": ref}))
        cur["id"] = 2  # a new conversation
        self.assertFalse(hub.update_art(pid, {"after": ref}))


class UnverifiedClaimTests(unittest.TestCase):
    def test_the_card_carries_the_agents_quote_check(self):
        result = {
            "question": "q",
            "summary": "s",
            "sources": [{"url": "https://example.org/a", "title": "A"}],
            "findings": [
                {"claim": "checked", "url": "https://example.org/a", "verified": True},
                {
                    "claim": "not found on the page",
                    "url": "https://example.org/a",
                    "verified": False,
                },
                {"claim": "older result", "url": "https://example.org/a"},
            ],
        }
        f = pn.research_card(result)["findings"]
        self.assertEqual([x.get("verified") for x in f], [True, False, None])
        errors: list[str] = []
        pn._v_research(
            {"findings": f, "sources": pn.research_card(result)["sources"], "images": []}, errors
        )
        self.assertEqual(errors, [])
