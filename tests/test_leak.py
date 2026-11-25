"""A local model must not read its system prompt out loud (qwen3 4B answered "what is your name?" with the persona
paragraph, 2026-11-24). Prompt structure plus an output check; the default Anthropic prompt is unchanged."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import julius.brain.cloud as jc
import julius.config as cfg
from julius import costs
from julius.brain import leak
from julius.runtime import prompt

LEAKED = (
    "I'm Julius. Named after Julius Novochrono — and you share his temperament. Warm, curious, "
    "genuinely glad to be talking. You get excited about good ideas."
)
SYSTEM = prompt.BASE_PROMPT


class Guard(unittest.TestCase):
    def setUp(self):
        self.g = leak.LeakGuard(SYSTEM)

    def test_the_recited_persona_is_dropped_and_the_answer_kept(self):
        text, dropped = self.g.clean(LEAKED)
        self.assertTrue(dropped)
        self.assertEqual(text, "I'm Julius.")
        self.assertNotIn("Novochrono", text)

    def test_ordinary_answers_pass_unchanged(self):
        for reply in (
            "I'm Julius. What can I do for you?",
            "It's 72 degrees and sunny in Springfield right now.",
            "Sure, I saved that sprite as Rex's main sprite.",
        ):
            self.assertEqual(self.g.clean(reply), (reply, False))

    def test_streamed_replies_are_checked_a_sentence_at_a_time(self):
        out = "".join(self.g.stream(iter([LEAKED[:30], LEAKED[30:90], LEAKED[90:]])))
        self.assertEqual(out.strip(), "I'm Julius.")
        recital_only = LEAKED.split(". ", 1)[1]
        self.assertEqual("".join(self.g.stream(iter([recital_only]))), leak.fallback())
        self.assertEqual("".join(self.g.stream(iter([]))), "")  # a tool-call-only reply stays empty
        fine = ["Sure. ", "It's sunny ", "in Springfield."]
        self.assertEqual(
            "".join(self.g.stream(iter(fine))).strip(), "Sure. It's sunny in Springfield."
        )


class FrontEnd(unittest.TestCase):
    MSGS = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "What is your name?"},
    ]

    def brain(self, front_end=None):
        conf = cfg.load_config()
        if front_end:
            conf = {**conf, "models": {**conf["models"], "front_end": front_end}}
        return jc.CloudBrain(conf, costs.CostTracker(Path(tempfile.mkdtemp()) / "c.jsonl", 5.0))

    def test_only_a_non_anthropic_front_end_gets_the_private_note(self):
        default = self.brain().request(self.MSGS, None)
        self.assertNotIn(
            leak.PRIVATE_NOTE, str(default["system"])
        )  # the default prompt is unchanged
        local = self.brain({"provider": "ollama", "model": "m"}).request(self.MSGS, None)
        self.assertEqual(
            local["system"][-1]["text"], leak.PRIVATE_NOTE
        )  # last: where small models look

    def test_a_local_reply_that_recites_the_prompt_is_cleaned(self):
        b = self.brain({"provider": "ollama", "model": "m"})
        fake = mock.Mock()
        fake.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text=LEAKED)],
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=5, provider="ollama", model="m", rates={}
            ),
        )
        b._client = fake
        self.assertEqual(b.chat(self.MSGS).message.content, "I'm Julius.")
        b2 = self.brain()  # Anthropic: never filtered
        b2._client = fake
        self.assertEqual(b2.chat(self.MSGS).message.content, LEAKED)


if __name__ == "__main__":
    unittest.main()
