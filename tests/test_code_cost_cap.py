"""The coding task stops at its dollar cap while it runs (2026-11-26: cap_hit overshot, $0.158 on a $0.10 cap).

The CLI reports its cost only in its final result, and Julius used to stop a task only at 1.25x the cap from that report,
so a few expensive turns ran straight past the cap. Now each streamed message's token usage is priced as it arrives."""

import json
import unittest

import julius.agents.code_agent as ca
from tests.test_code_agent import Base

# one Opus 5.5 turn: 2,000 input, 1,000 output and 1,500 cache-read tokens = 0.008 + 0.020 + 0.0003 = $0.0283
TURN_USAGE = {
    "input_tokens": 2000,
    "output_tokens": 1000,
    "cache_read_input_tokens": 1500,
    "cache_creation_input_tokens": 0,
}


START_USAGE = {
    **TURN_USAGE,
    "output_tokens": 5,
}  # what the CLI's whole-message event reports: usage at the START


def event(i):
    """The whole message, as the CLI streams it (its usage is from the start of the message, a few output tokens)."""
    return {
        "type": "assistant",
        "message": {
            "id": f"msg_{i}",
            "model": "claude-opus-5-5",
            "role": "assistant",
            "content": [],
            "usage": START_USAGE,
        },
    }


def stream(ev):
    return {"type": "stream_event", "event": ev}


CHUNK = (
    "x" * 250
)  # 100 estimated tokens at 2.5 characters a token; ten of them are the turn's 1,000 output tokens


def partials(i):
    """--include-partial-messages, as the CLI sends it: the output token count comes only at the END of the message."""
    text = {"type": "text_delta", "text": CHUNK}
    return [
        stream(
            {
                "type": "message_start",
                "message": {"id": f"msg_{i}", "model": "claude-opus-5-5", "usage": START_USAGE},
            }
        ),
        stream(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        ),
        *[stream({"type": "content_block_delta", "index": 0, "delta": text}) for _ in range(10)],
        stream({"type": "content_block_stop", "index": 0}),
        stream(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 1000},
            }
        ),
        stream({"type": "message_stop"}),
    ]


class CostCap(Base):
    def stand_in(self, turns=6, final=0.158):
        """A scripted `claude`: `turns` costly messages, streamed in parts, then its final result with the CLI's own total."""
        lines = [
            json.dumps(e)
            for i in range(turns)
            for e in (*partials(i)[:2], event(i), *partials(i)[2:])
        ]
        script = self.tmp / "fake_claude.sh"
        body = "#!/bin/sh\ntrap '' TERM\n" + "".join(
            f"echo '{line}'\nsleep 0.02\n" for line in lines
        )
        body += (
            "echo '"
            + json.dumps(
                {"type": "result", "subtype": "success", "total_cost_usd": final, "result": "done"}
            )
            + "'\n"
        )
        script.write_text(body)
        script.chmod(0o755)
        return script

    def test_a_task_is_stopped_at_its_cap_not_after_it(self):
        t = self.task(cap_usd=0.10)
        run = ca.start(
            t, api_key="sk-test", config=self.cfg, claude=str(self.stand_in()), sandbox=False
        )
        self.assertTrue(run.wait(30))
        self.assertEqual(t.status, "capped")
        # stopped inside the message that reached the cap, within a chunk of it, never the CLI's $0.158
        self.assertGreaterEqual(t.cost_usd, 0.10)
        self.assertLess(t.cost_usd, 0.10 + 0.003)

    def test_a_task_under_its_cap_runs_to_the_end_and_keeps_the_cli_total(self):
        t = self.task(cap_usd=1.0)
        run = ca.start(
            t,
            api_key="sk-test",
            config=self.cfg,
            claude=str(self.stand_in(turns=3, final=0.09)),
            sandbox=False,
        )
        self.assertTrue(run.wait(30))
        self.assertEqual(t.status, "done")
        self.assertAlmostEqual(t.cost_usd, 0.09, places=3)

    def test_turns_are_priced_from_their_token_usage_once_each(self):
        self.assertAlmostEqual(ca.message_cost(TURN_USAGE, "claude-opus-5-5"), 0.0283, places=4)
        five_min = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 1000,
            "cache_creation": {"ephemeral_5m_input_tokens": 1000},
        }
        one_hour = {**five_min, "cache_creation": {"ephemeral_1h_input_tokens": 1000}}
        self.assertAlmostEqual(ca.message_cost(five_min, "claude-opus-5-5"), 0.005, places=6)
        self.assertAlmostEqual(ca.message_cost(one_hour, "claude-opus-5-5"), 0.008, places=6)
        meter = ca.CostMeter()
        for e in [*partials(1), event(1), event(1)]:  # repeats of one message are counted once
            meter.add(e)
        self.assertAlmostEqual(meter.usd, 0.0283, places=4)
        meter.add(event(2))  # with no partials, only the start-of-message usage is known
        self.assertAlmostEqual(meter.usd, 0.0283 + ca.message_cost(START_USAGE), places=4)

    def test_whole_message_events_alone_would_hide_the_cost(self):
        """The 2026-11-26 failure: priced from whole-message events only, six $0.028 turns looked like $0.05."""
        meter = ca.CostMeter()
        for i in range(6):
            meter.add(event(i))
        self.assertLess(meter.usd, 0.10)
        for i in range(6):
            for e in partials(i):
                meter.add(e)
        self.assertAlmostEqual(meter.usd, 6 * 0.0283, places=4)

    def test_a_message_is_estimated_while_it_is_written(self):
        now = [0.0]
        meter = ca.CostMeter(clock=lambda: now[0])
        for e in partials(1)[
            :7
        ]:  # start, block start, five 250-character chunks: about 500 output tokens so far
            meter.add(e)
        self.assertAlmostEqual(
            meter.usd, ca.message_cost({**TURN_USAGE, "output_tokens": 500}), places=4
        )
        meter.add(
            stream(
                {"type": "content_block_start", "index": 1, "content_block": {"type": "thinking"}}
            )
        )
        now[0] = 3.0  # three seconds of thinking, which streams no text: about 300 more tokens
        self.assertAlmostEqual(
            meter.usd, ca.message_cost({**TURN_USAGE, "output_tokens": 800}), places=4
        )
        meter.add(stream({"type": "content_block_stop", "index": 1}))
        meter.add(stream({"type": "message_delta", "usage": {"output_tokens": 1500}}))
        now[0] = 60.0  # the real count replaces the estimate, and time no longer adds
        self.assertAlmostEqual(
            meter.usd, ca.message_cost({**TURN_USAGE, "output_tokens": 1500}), places=5
        )

    def test_it_stops_between_turns_when_the_next_request_would_cross_the_cap(self):
        meter = ca.CostMeter()
        for e in partials(
            1
        ):  # ends in a tool call, so the CLI will send another request with this context again
            meter.add(e)
        self.assertAlmostEqual(
            meter.next_turn_usd, ca.message_cost({**TURN_USAGE, "output_tokens": 0}), places=6
        )
        meter.add(partials(2)[0])  # the next request started: nothing more to predict until it ends
        self.assertEqual(meter.next_turn_usd, 0.0)
        last = [
            *partials(2)[1:-2],
            stream(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 1000},
                }
            ),
        ]
        for e in last:  # the final answer: no request follows it
            meter.add(e)
        self.assertEqual(meter.next_turn_usd, 0.0)

    def test_the_cli_is_asked_for_partial_messages(self):
        argv, _env, _cwd = ca.build_command(self.task(), "sk-test", config=self.cfg)
        self.assertIn("--include-partial-messages", argv)


if __name__ == "__main__":
    unittest.main()
