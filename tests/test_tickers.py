"""Unit tests for tickers."""

import unittest
from unittest import mock

import julius.brain.tickers as tk
import julius.brain.tools as jt
from julius.paths import REPO_ROOT


def cand(symbol, name, type_="EQUITY"):
    return {"symbol": symbol, "name": name, "type": type_}


class Resolve(unittest.TestCase):
    def no_net(self, q):
        raise AssertionError(f"the table should have answered {q!r} without a lookup")

    def test_common_names_resolve_from_the_table_without_a_lookup(self):
        for name, sym in (
            ("Apple", "AAPL"),
            ("apple", "AAPL"),
            ("APPLE", "AAPL"),
            ("Apple Inc.", "AAPL"),
            ("Apple stock", "AAPL"),
            ("Microsoft", "MSFT"),
            ("Alphabet", "GOOGL"),
            ("Google", "GOOGL"),
            ("Home Depot", "HD"),
            ("The Home Depot", "HD"),
            ("Johnson & Johnson", "JNJ"),
            ("McDonald's", "MCD"),
            ("Lowe's", "LOW"),
            ("Coca-Cola", "KO"),
            ("S&P 500", "SPY"),
            ("Berkshire Hathaway", "BRK-B"),
            ("Bitcoin", "BTC-USD"),
            ("ethereum", "ETH-USD"),
            ("Dogecoin", "DOGE-USD"),
            ("Solana", "SOL-USD"),
            ("btc", "BTC-USD"),
            ("aapl", "AAPL"),
        ):
            r = tk.resolve(name, search=self.no_net)
            self.assertEqual((r.symbol, r.how), (sym, "table"), name)

    def test_real_tickers_pass_through_untouched(self):
        for t in ("AAPL", "TSLA", "SPY", "F", "BRK-B", "^GSPC", "BTC-USD", "ES=F", "RDS.A"):
            r = tk.resolve(t, search=self.no_net)
            self.assertEqual(r.symbol, t)

    def test_an_unknown_name_is_looked_up_and_a_single_clear_match_is_used(self):
        r = tk.resolve(
            "Cheesecake Factory",
            search=lambda q: [
                cand("CAKE", "The Cheesecake Factory Incorporated"),
                cand("CAKE.MX", "The Cheesecake Factory Incorporated"),
            ],
        )
        self.assertEqual((r.symbol, r.how, r.ambiguous), ("CAKE", "lookup", []))

    def test_several_different_companies_are_ambiguous_and_listed(self):
        r = tk.resolve(
            "Delta",
            search=lambda q: [
                cand("DAL", "Delta Air Lines, Inc."),
                cand("DLTA", "Delta Apparel Inc."),
                cand("DLT", "Delta Dental"),
                cand("DX", "Another Delta"),
            ],
        )
        self.assertIsNone(r.symbol)
        self.assertEqual([c["symbol"] for c in r.ambiguous], ["DAL", "DLTA", "DLT"])
        msg = tk.ambiguity_message("Delta", r.ambiguous)
        self.assertTrue(msg.startswith("AMBIGUOUS"))
        self.assertIn("Ask the user", msg)
        self.assertIn("Delta Air Lines, Inc. (DAL)", msg)

    def test_a_name_that_equals_one_candidate_is_still_ambiguous_when_there_are_others_and_us_listings_come_first(
        self,
    ):
        r = tk.resolve(
            "Delta",
            search=lambda q: [
                cand("DELTACORP.NS", "Delta Corp Limited"),
                cand("2308.TW", "Delta Electronics, Inc."),
                cand("DAL", "Delta Air Lines, Inc."),
            ],
        )
        self.assertIsNone(r.symbol)
        self.assertEqual([c["symbol"] for c in r.ambiguous], ["DAL", "DELTACORP.NS", "2308.TW"])

    def test_the_lookup_is_given_the_cleaned_name(self):
        seen = []
        tk.resolve("Delta stock", search=lambda q: seen.append(q) or [])
        self.assertEqual(seen, ["delta"])

    def test_non_securities_are_ignored_and_nothing_found_is_not_a_guess(self):
        self.assertEqual(
            tk.resolve(
                "Blorp",
                search=lambda q: [
                    cand("X", "Blorp Futures", "FUTURE"),
                    cand("Y", "Blorp Option", "OPTION"),
                ],
            ).symbol,
            None,
        )
        r = tk.resolve("Blorp", search=lambda q: [])
        self.assertEqual((r.symbol, r.ambiguous), (None, []))

    def test_a_network_failure_is_not_found_not_a_crash(self):
        with mock.patch("requests.get", side_effect=OSError("down")):
            self.assertEqual(tk.yahoo_search("anything"), [])

    def test_no_table_symbol_is_malformed_and_names_are_unique(self):
        for v in list(tk.STOCKS.values()) + list(tk.CRYPTO.values()):
            self.assertRegex(v, r"^[A-Z0-9.\-]{1,6}$")
        seen = {}
        for k, v in tk.STOCKS.items():
            self.assertEqual(
                seen.setdefault(tk.normalize(k), v), v, f"{k} collides with another name"
            )


class QuoteTool(unittest.TestCase):
    def fake_get(self, seen, price=190.5, missing=("NOPE",)):
        def get(url, params=None, headers=None, timeout=None):
            seen.append(url)

            class R:
                def json(_):
                    return {
                        "chart": {
                            "result": None
                            if url.rsplit("/", 1)[1] in missing
                            else [
                                {
                                    "meta": {
                                        "regularMarketPrice": price,
                                        "chartPreviousClose": 188.0,
                                        "shortName": "Thing",
                                        "currency": "USD",
                                    }
                                }
                            ]
                        }
                    }

            return R()

        return get

    def test_apple_becomes_aapl_before_the_price_request(self):
        seen = []
        with mock.patch("requests.get", self.fake_get(seen)):
            out = jt.get_quote("Apple")
        self.assertEqual(seen, ["https://query1.finance.yahoo.com/v8/finance/chart/AAPL"])
        self.assertIn("(AAPL)", out)

    def test_bitcoin_uses_the_usd_pair(self):
        seen = []
        with mock.patch("requests.get", self.fake_get(seen)):
            jt.get_quote("Bitcoin")
            jt.get_quote("BTC")
        self.assertEqual(seen, ["https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD"] * 2)

    def test_ambiguous_names_make_the_tool_ask_instead_of_guessing(self):
        seen = []
        with (
            mock.patch(
                "julius.brain.tickers.yahoo_search",
                lambda q: [cand("DAL", "Delta Air Lines"), cand("DLTA", "Delta Apparel")],
            ),
            mock.patch("requests.get", self.fake_get(seen)),
        ):
            out = jt.get_quote("Delta")
        self.assertTrue(out.startswith("AMBIGUOUS"))
        self.assertEqual(seen, [], "no price is fetched for a guess")

    def test_unknown_name_not_found_says_so(self):
        with (
            mock.patch("julius.brain.tickers.yahoo_search", lambda q: []),
            mock.patch("requests.get", self.fake_get([])),
        ):
            self.assertEqual(jt.get_quote("Blorp"), "No data for Blorp.")

    def test_a_name_typed_in_capitals_that_has_no_data_is_retried_as_a_name(self):
        seen = []
        with (
            mock.patch(
                "julius.brain.tickers.yahoo_search",
                lambda q: [cand("CAKE", "The Cheesecake Factory")],
            ),
            mock.patch("requests.get", self.fake_get(seen, missing=("CHEESECAKE",))),
        ):
            out = jt.get_quote("CHEESECAKE")
        self.assertIn("(CAKE)", out)
        self.assertEqual([u.rsplit("/", 1)[1] for u in seen], ["CHEESECAKE", "CAKE"])

    def test_a_ticker_shaped_word_that_is_nothing_is_not_found(self):
        with (
            mock.patch("julius.brain.tickers.yahoo_search", lambda q: []),
            mock.patch("requests.get", self.fake_get([])),
        ):
            self.assertEqual(jt.get_quote("NOPE"), "No data for NOPE.")

    def test_validation_accepts_names_and_still_refuses_junk(self):
        import julius.brain.dispatcher as d

        v = d.Dispatcher({"get_quote": jt.get_quote}, jt.SCHEMAS)._validate
        for ok in ("AAPL", "Home Depot", "Johnson & Johnson", "McDonald's", "BRK-B"):
            v("get_quote", {"symbol": ok})
        for bad in ("", "a;rm -rf", "x" * 41, "$(ls)", "<script>"):
            with self.assertRaises(ValueError):
                v("get_quote", {"symbol": bad})


class ForcedFreshCalls(unittest.TestCase):
    def test_live_requests_force_a_tool_even_when_the_topic_came_up_before(self):
        for q in (
            "What's the score of the Sharks game?",
            "Who won the NHL game last night?",
            "How much is Bitcoin right now?",
            "What's the weather in Shelbyville?",
            "What time does the Sharks game start tonight?",
            "What's the stock price of Apple?",
            "What's the price of Microsoft?",
            "Julius, what's the latest news?",
            "Is it raining?",
        ):
            self.assertTrue(jt.live_tool_required(q), q)

    def test_chat_questions_and_non_requests_do_not(self):
        for q in (
            "Tell me a joke",
            "Thanks for the score",
            "I like the Sharks",
            "What's two plus two",
            "How are you?",
            "What's traffic like on highway 99 right now?",
            "What's the price of gas in Springfield right now?",
        ):
            self.assertFalse(jt.live_tool_required(q), q)

    def test_cloud_request_sets_tool_choice_any_only_when_asked(self):
        from julius.brain import cloud

        brain = cloud.CloudBrain.__new__(cloud.CloudBrain)
        brain.model = "m"
        brain._last_tools = None
        brain.tool_schemas = lambda t: t
        a = brain.request([{"role": "user", "content": "hi"}], jt.SCHEMAS[:1], force_tool=True)
        b = brain.request([{"role": "user", "content": "hi"}], jt.SCHEMAS[:1])
        self.assertEqual(a.get("tool_choice"), {"type": "any"})
        self.assertNotIn("tool_choice", b)

    def test_force_flag_is_removed_before_the_local_fallback_sees_the_options(self):
        from julius.brain import cloud

        seen = {}
        brain = cloud.CloudBrain.__new__(cloud.CloudBrain)
        brain.auth_bad = True
        brain.auth_pending = False
        brain._offline = lambda m, t, s, o, announce: seen.update(o) or "x"
        brain.chat([], tools=None, stream=False, options={"temperature": 0.4, "force_tool": True})
        self.assertEqual(seen, {"temperature": 0.4})

    def test_candidate_forces_only_the_first_call_of_the_turn(self):
        import inspect

        from julius.runtime import brain_turn

        src = inspect.getsource(brain_turn.BrainTurn.model_tool_message)
        self.assertIn("jt.live_tool_required(self.user_text)", src)
        self.assertIn("livedata.tool_result_this_turn", src)
        self.assertIn(
            'options["force_tool"] = True', inspect.getsource(brain_turn.BrainTurn.stream)
        )

    def test_prompt_tells_him_earlier_answers_are_stale(self):
        self.assertIn(
            "call the tool again even if the same topic came up earlier",
            (REPO_ROOT / "julius/audio/speech_text.py").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
