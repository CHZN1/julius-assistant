"""Identify-and-shop ("what is this and where can I buy one" with an image dropped) and nutrition questions about what you hold."""

import unittest
from types import SimpleNamespace
from unittest import mock

from julius.agents import research, vision


class Routing(unittest.TestCase):
    def test_shop_needs_a_dropped_image_or_the_camera(self):
        r = vision.parse("What is this keyboard and where can I buy one?", has_dropped=True)
        self.assertEqual((r.source, r.shop, r.closely), ("image", True, True))
        self.assertIsNone(vision.parse("where can I buy oat milk", has_dropped=False))
        r = vision.parse("What am I holding and where can I buy it?")
        self.assertEqual((r.source, r.shop), ("camera", True))

    def test_nutrition_about_what_i_hold_is_the_closer_look(self):
        r = vision.parse("How many calories are in what I'm holding?")
        self.assertEqual((r.source, r.closely, r.shop), ("camera", True, False))


class Shop(unittest.TestCase):
    def brain(self):
        return SimpleNamespace(
            config={"models": {"sonnet": "claude-sonnet-x", "haiku": "claude-haiku-x"}}
        )

    def test_identify_then_research_the_stock_model(self):
        ident = '{"name": "Yamaha PSR-E473", "details": "61 keys, two speakers", "customized": "pink paint", "confidence": "medium", "search": "Yamaha PSR-E473"}'
        panel = mock.Mock()
        files = mock.Mock(put_memory=mock.Mock(return_value="/p/x.jpg"))
        with (
            mock.patch.object(vision, "ask_model", return_value=(ident, 0.01)),
            mock.patch.object(
                research,
                "research_task",
                return_value="It sells for about $350 at Sweetwater. [research: 3 cited finding(s), sources: 1 Sweetwater; saved as x]",
            ) as rt,
        ):
            said = vision.shop(
                vision.Look("image", True, "what is this and where can I buy one", shop=True),
                self.brain(),
                b"jpg",
                10,
                10,
                "Dropped image",
                panel,
                files,
                4,
            )
        self.assertEqual(
            said,
            "I think that's the Yamaha PSR-E473. It looks customized: pink paint. It sells for about $350 at Sweetwater.",
        )
        q = rt.call_args[0][0]["question"]
        self.assertIn("Yamaha PSR-E473", q)
        self.assertIn("stock version", q)
        card = panel.vision.call_args[0][0]
        self.assertEqual(card["title"], "Identified")
        self.assertIn("Confidence: medium.", card["answer"])

    def test_nothing_usable_is_said_plainly_and_nothing_is_searched(self):
        with (
            mock.patch.object(vision, "ask_model", return_value=("no idea", 0.01)),
            mock.patch.object(research, "research_task") as rt,
        ):
            said = vision.shop(
                vision.Look("image", True, "where can I buy this", shop=True),
                self.brain(),
                b"j",
                1,
                1,
                "x",
                None,
                None,
                1,
            )
        self.assertIn("couldn't tell what that is", said)
        rt.assert_not_called()

    def test_nutrition_rule_reaches_the_model(self):
        client = mock.Mock()
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )
        brain = SimpleNamespace(
            config={"models": {"sonnet": "s"}}, slow_client=lambda: client, tracker=mock.Mock()
        )
        with mock.patch("julius.agents.vision.costs.cost_of", return_value=0.0):
            vision.ask_model(
                brain, "sonnet", b"j", "how many calories are in what I'm holding", "camera"
            )
        self.assertIn("nutrition label", client.messages.create.call_args.kwargs["system"])


if __name__ == "__main__":
    unittest.main()
