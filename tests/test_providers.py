"""Bring your own model: every adapter turns the same conversation into the same normalized events, tiers resolve from
julius.toml (old configs unchanged), the cost ledger records provider and model, and the front end runs on a local model
with no Anthropic key. Recorded responses (tests/fixtures/providers, captured from Ollama 0.32) replace the network."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

import julius.brain.cloud as jc
from julius import costs
from julius.brain import providers
from julius.brain.providers import base, ollama, openai_compat, shim
from julius.brain.providers.anthropic import AnthropicProvider, TaggingClient
from julius.brain.providers.base import Done, TextDelta, ToolCall

FIX = Path(__file__).resolve().parent / "fixtures" / "providers"
WEATHER = {
    "name": "get_weather",
    "description": "Current weather for a city",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    "cache_control": {"type": "ephemeral"},
}
ASK = {
    "model": "claude-haiku-5-5",
    "max_tokens": 200,
    "system": [
        {"type": "text", "text": "Use tools for live data.", "cache_control": {"type": "ephemeral"}}
    ],
    "messages": [{"role": "user", "content": "What's the weather in Springfield right now?"}],
    "tools": [WEATHER],
    "thinking": {"type": "disabled"},
    "output_config": {"effort": "low"},
}


def replay(name, status=200, seen=None):
    """An httpx transport answering every request with a recorded body (and keeping the request bodies)."""

    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        kind = {".json": "application/json", ".sse": "text/event-stream"}.get(
            Path(name).suffix, "application/x-ndjson"
        )
        return httpx.Response(
            status,
            content=(FIX / name).read_bytes() if name else b"{}",
            headers={"content-type": kind},
        )

    return httpx.MockTransport(handler)


def shape(events):
    """Events without the provider-specific call ids and token counts: what must be identical across adapters."""
    out = []
    for e in events:
        if isinstance(e, ToolCall):
            out.append(("tool", e.name, e.input))
        elif isinstance(e, Done):
            out.append(("done", e.stop_reason))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    return text, out


class FakeSDK:
    """The Anthropic SDK's surface for one recorded reply (a get_weather call)."""

    def __init__(self, content, stop="tool_use"):
        reply = SimpleNamespace(
            content=content,
            stop_reason=stop,
            model="claude-haiku-5-5",
            usage=SimpleNamespace(input_tokens=166, output_tokens=21),
        )
        self.messages = SimpleNamespace(create=lambda **kw: reply)


TOOL_EVENTS = [("tool", "get_weather", {"city": "Springfield"}), ("done", "tool_use")]


class SameEventsFromEveryAdapter(unittest.TestCase):
    def test_a_tool_call_conversation(self):
        sdk = FakeSDK(
            [
                SimpleNamespace(
                    type="tool_use", id="toolu_1", name="get_weather", input={"city": "Springfield"}
                )
            ]
        )
        runs = {
            "anthropic": list(AnthropicProvider("claude-haiku-5-5", sdk).events(ASK)),
            "ollama once": list(
                ollama.OllamaProvider("q", transport=replay("ollama_tool_call_once.ndjson")).events(
                    ASK
                )
            ),
            "ollama stream": list(
                ollama.OllamaProvider(
                    "q", transport=replay("ollama_tool_call_stream.ndjson")
                ).events(ASK, stream=True)
            ),
            "openai once": list(
                openai_compat.OpenAICompatProvider(
                    "q", "http://x/v1", transport=replay("openai_tool_call_once.json")
                ).events(ASK)
            ),
            "openai stream": list(
                openai_compat.OpenAICompatProvider(
                    "q", "http://x/v1", transport=replay("openai_tool_call_stream.sse")
                ).events(ASK, stream=True)
            ),
            "openai split arguments": list(
                openai_compat.OpenAICompatProvider(
                    "q", "http://x/v1", transport=replay("openai_split_args_stream.sse")
                ).events(ASK, stream=True)
            ),
        }
        for name, events in runs.items():
            with self.subTest(name):
                self.assertEqual(shape(events), ("", TOOL_EVENTS))
                self.assertIsInstance(events[-1], Done)

    def test_a_plain_text_reply(self):
        for name, provider in (
            ("ollama", ollama.OllamaProvider("q", transport=replay("ollama_text_stream.ndjson"))),
            (
                "openai",
                openai_compat.OpenAICompatProvider(
                    "q", "http://x/v1", transport=replay("openai_text_stream.sse")
                ),
            ),
        ):
            with self.subTest(name):
                text, out = shape(list(provider.events(ASK, stream=True)))
                self.assertTrue(text.strip())
                self.assertEqual(out, [("done", "end_turn")])

    def test_usage_is_normalized(self):
        *_, done = openai_compat.OpenAICompatProvider(
            "q", "http://x/v1", transport=replay("openai_split_args_stream.sse")
        ).events(ASK, stream=True)
        self.assertEqual(
            (done.usage.input_tokens, done.usage.cache_read_input_tokens, done.usage.output_tokens),
            (66, 100, 21),
        )
        *_, done = ollama.OllamaProvider(
            "q", transport=replay("ollama_tool_call_once.ndjson")
        ).events(ASK)
        self.assertGreater(done.usage.input_tokens, 0)
        self.assertGreater(done.usage.output_tokens, 0)


CONVERSATION = {
    **ASK,
    "messages": [
        {"role": "user", "content": "What's the weather in Springfield?"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Checking."},
                {
                    "type": "tool_use",
                    "id": "toolu_julius_1",
                    "name": "get_weather",
                    "input": {"city": "Springfield"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_julius_1",
                    "content": "72F and sunny",
                },
                {"type": "text", "text": "SESSION STATE (true): nothing made yet"},
            ],
        },
    ],
    "tool_choice": {"type": "tool", "name": "get_weather"},
}


class Translation(unittest.TestCase):
    def test_openai_messages_tools_and_tool_choice(self):
        body = openai_compat.body(CONVERSATION, "m", stream=True)
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])
        self.assertEqual(body["messages"][2]["tool_calls"][0]["id"], "toolu_julius_1")
        self.assertEqual(
            json.loads(body["messages"][2]["tool_calls"][0]["function"]["arguments"]),
            {"city": "Springfield"},
        )
        self.assertEqual(
            body["messages"][3],
            {"role": "tool", "tool_call_id": "toolu_julius_1", "content": "72F and sunny"},
        )
        self.assertEqual(
            body["tool_choice"], {"type": "function", "function": {"name": "get_weather"}}
        )
        self.assertEqual(body["tools"][0]["function"]["name"], "get_weather")
        self.assertNotIn("cache_control", json.dumps(body))
        self.assertNotIn("thinking", body)
        self.assertEqual(openai_compat.to_tool_choice({"type": "any"}), "required")
        self.assertEqual(openai_compat.to_tool_choice({"type": "none"}), "none")

    def test_an_endpoint_without_tool_choice_gets_an_instruction_instead(self):
        body = openai_compat.body(CONVERSATION, "m", stream=False, forced=False)
        self.assertNotIn("tool_choice", body)
        self.assertIn(
            "Answer this turn by calling the get_weather tool.", body["messages"][0]["content"]
        )

    def test_ollama_messages_name_the_tool_and_carry_images(self):
        body = ollama.body(CONVERSATION, "m", stream=False, options={"options": {"num_ctx": 8192}})
        self.assertEqual(
            [m["role"] for m in body["messages"]], ["system", "user", "assistant", "tool", "user"]
        )
        self.assertEqual(body["messages"][3]["tool_name"], "get_weather")
        self.assertEqual(
            body["messages"][2]["tool_calls"][0]["function"]["arguments"], {"city": "Springfield"}
        )
        self.assertIn("calling the get_weather tool", body["messages"][0]["content"])
        self.assertEqual(body["options"], {"num_predict": 200, "num_ctx": 8192})
        pic = {
            **ASK,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": "QUJD",
                            },
                        },
                        {"type": "text", "text": "What is this?"},
                    ],
                }
            ],
        }
        self.assertEqual(ollama.body(pic, "m", False, {})["messages"][1]["images"], ["QUJD"])
        content = openai_compat.body(pic, "m", False)["messages"][1]["content"]
        self.assertEqual(content[0]["image_url"]["url"], "data:image/jpeg;base64,QUJD")

    def test_tool_choice_none_offers_no_tools(self):
        quiet = {**CONVERSATION, "tool_choice": {"type": "none"}}
        self.assertNotIn("tools", ollama.body(quiet, "m", False, {}))
        self.assertEqual(openai_compat.body(quiet, "m", False)["tool_choice"], "none")

    def test_invalid_tool_json_is_counted_not_fatal(self):
        stats = {}
        self.assertEqual(base.parse_arguments('{"city": "Spring', stats), {})
        self.assertEqual(
            base.parse_arguments('{"city": "Springfield"}', stats), {"city": "Springfield"}
        )
        self.assertEqual(stats, {"tool_calls": 2, "invalid_json": 1})


class Errors(unittest.TestCase):
    def events(self, provider):
        return list(provider.events(ASK))

    def test_auth_unavailable_and_bad_requests_are_told_apart(self):
        for status, error in (
            (401, base.ProviderAuthError),
            (503, base.ProviderUnavailable),
            (429, base.ProviderUnavailable),
            (400, base.ProviderError),
        ):
            with self.subTest(status):
                p = openai_compat.OpenAICompatProvider(
                    "m", "http://x/v1", transport=replay("", status)
                )
                with self.assertRaises(error):
                    self.events(p)
        with self.assertRaisesRegex(base.ProviderError, "ollama pull m"):
            self.events(ollama.OllamaProvider("m", transport=replay("", 404)))

    def test_a_server_that_is_not_running_is_unavailable(self):
        def refuse(request):
            raise httpx.ConnectError("refused")

        for p in (
            ollama.OllamaProvider("m", transport=httpx.MockTransport(refuse)),
            openai_compat.OpenAICompatProvider(
                "m", "http://x/v1", transport=httpx.MockTransport(refuse)
            ),
        ):
            with self.subTest(p.name), self.assertRaises(base.ProviderUnavailable):
                self.events(p)


OLD = {
    "models": {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"},
    "code": {"model": "claude-opus-5-5"},
}


class Tiers(unittest.TestCase):
    def test_an_old_config_resolves_exactly_as_before(self):
        got = {t: (m.provider, m.model) for t, m in providers.configured(OLD).items()}
        self.assertEqual(
            got,
            {
                "front_end": ("anthropic", "claude-haiku-5-5"),
                "planner": ("anthropic", "claude-sonnet-5-5"),
                "research": ("anthropic", "claude-haiku-5-5"),
                "vision": ("anthropic", "claude-sonnet-5-5"),
                "coding": ("claude-cli", "claude-opus-5-5"),
            },
        )
        self.assertEqual(providers.resolve("vision", OLD, legacy="haiku").model, "claude-haiku-5-5")
        self.assertEqual(providers.tier_of("sonnet"), "planner")

    def test_a_tier_block_and_presets(self):
        conf = {
            **OLD,
            "models": {
                **OLD["models"],
                "front_end": {"provider": "ollama", "model": "qwen3:4b"},
                "planner": {"provider": "lmstudio", "model": "qwen3-30b"},
                "research": {
                    "provider": "openrouter",
                    "model": "some/model",
                    "price_in": 0.2,
                    "price_out": 0.8,
                },
            },
        }
        fe, pl, rs = (
            providers.resolve(t, conf, env={}) for t in ("front_end", "planner", "research")
        )
        self.assertEqual((fe.adapter, fe.base_url), ("ollama", "http://localhost:11434"))
        self.assertEqual((pl.adapter, pl.base_url), ("openai_compat", "http://localhost:1234/v1"))
        self.assertEqual(
            (rs.api_key_env, rs.price_in, rs.price_out), ("OPENROUTER_API_KEY", 0.2, 0.8)
        )
        self.assertNotIn("KEY", rs.describe())

    def test_the_environment_overrides_a_tier_for_evals(self):
        env = {
            "JULIUS_TIER_FRONT_END": "ollama:qwen3:4b-instruct-2507-q4_K_M",
            "JULIUS_TIER_FRONT_END_BASE_URL": "http://box:11434",
        }
        tm = providers.resolve("front_end", OLD, env=env)
        self.assertEqual(
            (tm.provider, tm.model, tm.base_url),
            ("ollama", "qwen3:4b-instruct-2507-q4_K_M", "http://box:11434"),
        )

    def test_bad_configs_are_refused_at_start(self):
        for block, msg in (
            ({"provider": "openai"}, "not one of"),
            ({"provider": "ollama"}, "needs a model"),
            ({"provider": "openai_compat", "model": "m"}, "needs a base_url"),
            ({"provider": "ollama", "model": "m", "base_url": "file:///etc"}, "http"),
        ):
            with self.subTest(msg), self.assertRaisesRegex(ValueError, msg):
                providers.resolve("front_end", {"models": {"front_end": block}}, env={})
        with self.assertRaisesRegex(ValueError, "claude-cli for now"):
            providers.resolve(
                "coding", {"models": {"coding": {"provider": "ollama", "model": "m"}}}, env={}
            )

    def test_a_missing_hosted_key_is_an_auth_error(self):
        tm = providers.resolve(
            "planner", {"models": {"planner": {"provider": "openrouter", "model": "m"}}}, env={}
        )
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(base.ProviderAuthError):
            providers.build_provider(tm, 30)


class StartUp(unittest.TestCase):
    def test_julius_toml_with_a_bad_tier_is_refused_and_a_good_one_loads(self):
        from julius import config

        with tempfile.TemporaryDirectory() as d:
            toml = Path(d) / "julius.toml"
            env = {"JULIUS_LOCAL_CONFIG": str(Path(d) / "none.toml")}
            toml.write_text('[models.front_end]\nprovider = "ollama"\n')
            with (
                mock.patch.dict(os.environ, env),
                self.assertRaisesRegex(ValueError, "needs a model"),
            ):
                config.load_config(toml)
            toml.write_text('[models.front_end]\nprovider = "ollama"\nmodel = "qwen3:4b"\n')
            with mock.patch.dict(os.environ, env):
                conf = config.load_config(toml)
        self.assertEqual(
            conf["models"]["haiku"], "claude-haiku-5-5"
        )  # the old keys are still there
        self.assertEqual(
            providers.resolve("front_end", conf, env={}).describe(),
            "ollama:qwen3:4b at http://localhost:11434",
        )

    def test_the_boot_line_names_each_tier_and_never_a_key(self):
        from julius.runtime.boot import brain_line

        local = {"provider": "ollama", "model": "qwen3:4b"}
        conf = {
            **OLD,
            "models": {**OLD["models"], "front_end": local, "planner": local, "research": local},
        }
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-TEST-NOT-A-KEY"}):
            line = brain_line(conf)
            self.assertEqual(
                line, "front end ollama:qwen3:4b, planner ollama:qwen3:4b, research ollama:qwen3:4b"
            )
            self.assertIn("Anthropic key", brain_line(OLD))
            self.assertNotIn("sk-ant", brain_line(OLD))


class LedgerAndShim(unittest.TestCase):
    def tracker(self):
        return costs.CostTracker(Path(tempfile.mkdtemp()) / "costs.jsonl", 5.0)

    def test_every_row_records_provider_and_model_and_local_is_free(self):
        t = self.tracker()
        local = shim.MessagesClient(
            ollama.OllamaProvider("qwen3:4b", transport=replay("ollama_tool_call_once.ndjson"))
        )
        r = local.messages.create(**ASK)
        self.assertEqual([b.type for b in r.content], ["tool_use"])
        row = t.record("haiku", r.usage, note="test")
        self.assertEqual((row["provider"], row["model"], row["usd"]), ("ollama", "qwen3:4b", 0.0))
        priced = shim.MessagesClient(
            openai_compat.OpenAICompatProvider(
                "some/model", "http://x/v1", transport=replay("openai_split_args_stream.sse")
            ),
            rates={"in": 1.0, "out": 2.0},
        )
        with priced.messages.stream(**ASK) as s:
            self.assertEqual("".join(s.text_stream), "")
            final = s.get_final_message()
        row = t.record("haiku", final.usage)
        self.assertEqual(row["provider"], "openai_compat")
        self.assertAlmostEqual(row["usd"], (166 * 1.0 + 21 * 2.0) / 1e6)
        sdk = TaggingClient(FakeSDK([SimpleNamespace(type="text", text="hi")], stop="end_turn"))
        row = t.record("haiku", sdk.messages.create(model="claude-haiku-5-5").usage)
        self.assertEqual((row["provider"], row["model"]), ("anthropic", "claude-haiku-5-5"))
        self.assertGreater(row["usd"], 0)
        for line in t.path.read_text().splitlines():
            self.assertIn("provider", json.loads(line))


class FrontEndWithoutAnthropic(unittest.TestCase):
    def setUp(self):
        providers.reset()
        self.addCleanup(providers.reset)
        self.conf = {
            **OLD,
            "models": {**OLD["models"], "front_end": {"provider": "ollama", "model": "qwen3:4b"}},
            "offline": {"model": "llama3.2:3b", "retry_seconds": 60},
        }

    def brain(self, transport, offline=None):
        patch = mock.patch.object(providers, "TRANSPORT", transport)
        patch.start()
        self.addCleanup(patch.stop)
        tracker = costs.CostTracker(Path(tempfile.mkdtemp()) / "costs.jsonl", 5.0)
        never = mock.patch.object(
            jc, "make_client", side_effect=AssertionError("no Anthropic client")
        )
        never.start()
        self.addCleanup(never.stop)
        return jc.CloudBrain(self.conf, tracker, offline=offline), tracker

    def test_a_turn_streams_and_calls_tools_with_no_anthropic_key(self):
        seen = []
        brain, tracker = self.brain(replay("ollama_tool_call_stream.ndjson", seen=seen))
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "w",
                    "parameters": WEATHER["input_schema"],
                },
            }
        ]
        with mock.patch.object(brain, "tool_schemas", side_effect=lambda s: s):
            parts = list(
                brain.chat(
                    [{"role": "system", "content": "sys"}, {"role": "user", "content": "weather?"}],
                    tools,
                    stream=True,
                )
            )
        calls = [c for p in parts for c in (getattr(p.message, "tool_calls", None) or [])]
        self.assertEqual(
            [(c.function.name, c.function.arguments) for c in calls],
            [("get_weather", {"city": "Springfield"})],
        )
        self.assertEqual(seen[0]["model"], "qwen3:4b")
        row = json.loads(tracker.path.read_text().splitlines()[-1])
        self.assertEqual((row["provider"], row["model"], row["usd"]), ("ollama", "qwen3:4b", 0.0))

    def test_an_unreachable_local_server_falls_back_like_the_cloud_does(self):
        def refuse(request):
            raise httpx.ConnectError("refused")

        fell = []
        brain, _ = self.brain(
            httpx.MockTransport(refuse),
            offline=lambda m, t, s, o: (
                fell.append(1)
                or SimpleNamespace(message=SimpleNamespace(content="offline", tool_calls=None))
            ),
        )
        hi = [{"role": "user", "content": "hi"}]
        first = brain.chat(hi, None, stream=False)  # one failure fails only that turn
        self.assertEqual((first.message.content, fell), (jc.FAILED_TURN_MESSAGE, []))
        out = brain.chat(hi, None, stream=False)  # the second in a row goes to the offline model
        self.assertEqual((out.message.content, fell), ("offline", [1]))


class LocalLimits(unittest.TestCase):
    """A local model stuck repeating itself must not stall a turn (qwen3 4B on routing case r183: about one call in three
    ran to its 1,024-token budget for a minute, sometimes ending in an HTTP 500 from Ollama)."""

    def test_local_front_ends_get_an_output_cap_and_a_deadline(self):
        fe = providers.resolve(
            "front_end", {"models": {"front_end": {"provider": "ollama", "model": "m"}}}, env={}
        )
        pl = providers.resolve(
            "planner", {"models": {"planner": {"provider": "lmstudio", "model": "m"}}}, env={}
        )
        hosted = providers.resolve(
            "planner", {"models": {"planner": {"provider": "openrouter", "model": "m"}}}, env={}
        )
        mine = providers.resolve(
            "front_end",
            {
                "models": {
                    "front_end": {
                        "provider": "ollama",
                        "model": "m",
                        "options": {"max_output_tokens": 300},
                    }
                }
            },
            env={},
        )
        self.assertEqual(providers.limits_for(fe), {"max_output_tokens": 512, "deadline_s": 90.0})
        self.assertEqual(providers.limits_for(pl), {"deadline_s": 180.0})
        self.assertEqual(providers.limits_for(hosted), {"deadline_s": 120.0})
        self.assertEqual(providers.limits_for(mine)["max_output_tokens"], 300)

    def test_the_cap_reaches_the_request(self):
        self.assertEqual(
            ollama.body(ASK, "m", True, {"max_output_tokens": 50})["options"]["num_predict"], 50
        )
        self.assertEqual(ollama.body(ASK, "m", True, {})["options"]["num_predict"], 200)
        self.assertEqual(
            openai_compat.body({**ASK, "max_tokens": 4096}, "m", True, cap=512)["max_tokens"], 512
        )
        self.assertNotIn(
            "max_output_tokens", json.dumps(ollama.body(ASK, "m", True, {"max_output_tokens": 50}))
        )

    def test_a_reply_past_its_deadline_is_a_timeout(self):
        now = [0.0]
        deadline = base.Deadline(45.0, clock=lambda: now[0])

        def slow_lines():
            for line in (FIX / "ollama_text_stream.ndjson").read_text().splitlines():
                now[0] += 10.0
                yield line

        with self.assertRaises(base.ProviderTimeout):
            list(ollama.OllamaProvider("m")._from_lines(slow_lines(), deadline))
        self.assertTrue(issubclass(base.ProviderTimeout, base.ProviderUnavailable))

    def test_an_unparseable_tool_call_is_bad_output_not_an_outage(self):
        def parse_error(request):
            return httpx.Response(500, content=b'{"error":"error parsing tool call: raw=..."}')

        with self.assertRaises(base.ProviderBadOutput):
            list(ollama.OllamaProvider("m", transport=httpx.MockTransport(parse_error)).events(ASK))
        mid = b'{"message":{"role":"assistant","content":"Hi"},"done":false}\n{"error":"error parsing tool call"}\n'
        stream_error = httpx.MockTransport(lambda r: httpx.Response(200, content=mid))
        with self.assertRaises(base.ProviderBadOutput):
            list(ollama.OllamaProvider("m", transport=stream_error).events(ASK, stream=True))

    def test_bad_output_fails_the_turn_without_counting_toward_offline(self):
        providers.reset()
        self.addCleanup(providers.reset)
        conf = {
            **OLD,
            "models": {**OLD["models"], "front_end": {"provider": "ollama", "model": "m"}},
            "offline": {"model": "x", "retry_seconds": 60, "failures_before_offline": 2},
        }
        t = httpx.MockTransport(
            lambda r: httpx.Response(500, content=b'{"error":"error parsing tool call"}')
        )
        with mock.patch.object(providers, "TRANSPORT", t):
            brain = jc.CloudBrain(
                conf,
                costs.CostTracker(Path(tempfile.mkdtemp()) / "c.jsonl", 5.0),
                offline=lambda *a: self.fail("bad output never goes offline"),
            )
            for _ in range(3):
                out = brain.chat([{"role": "user", "content": "hi"}], None)
                self.assertEqual(
                    (out.message.content, out.message.failed), (jc.FAILED_TURN_MESSAGE, True)
                )
            spoken = "".join(
                p.message.content
                for p in brain.chat([{"role": "user", "content": "hi"}], None, stream=True)
            )
        self.assertEqual(
            (spoken, brain.failures, brain.offline_until), (jc.FAILED_TURN_MESSAGE, 0, 0.0)
        )


if __name__ == "__main__":
    unittest.main()
