"""Stage 5c-art item 3: editable sprites. The layered document, parameters, words, undo/redo, the .julius-sprite file, card updates in place, the card
buttons, and the editor window's channel (GET/POST /v1/sprite). Synthetic face and mask, so no Vision helper is needed."""

import copy
import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sprite import synthetic  # noqa: E402

import julius.ui_bridge.charts as charts  # noqa: E402
import julius.ui_bridge.events as ev  # noqa: E402
import julius.ui_bridge.panel as pn  # noqa: E402
import julius.ui_bridge.panel_emit as pe  # noqa: E402
import julius.ui_bridge.panelfiles as pf  # noqa: E402
import julius.ui_bridge.uichannel as uc  # noqa: E402
from julius.art import commands, source, sprite  # noqa: E402
from julius.art import sprite_doc as sd  # noqa: E402
from julius.art import tool as art_tool  # noqa: E402


def built(**kw):
    img, face, mask = synthetic(beard=kw.pop("beard_drawn", "full"))
    return sprite.build(img, face, mask, sprite.Options(**kw))


class DocumentTests(unittest.TestCase):
    def test_the_document_renders_exactly_the_built_sprite(self):
        for kw in (
            {},
            {"head": 16},
            {"chibi": True},
            {"palette": "pico8", "big_eyes": True, "nose": True},
            {"head": 32, "palette": "nes"},
        ):
            sp = built(**kw)
            self.assertTrue(np.array_equal(np.asarray(sp.image), np.asarray(sd.render(sp.doc))), kw)

    def test_layers_are_named_in_drawing_order_and_palette_locked(self):
        doc = built(palette="pico8").doc
        lay = sd.layers(doc)
        self.assertEqual(tuple(lay), sd.LAYERS)
        n = len(doc["colors"])
        self.assertTrue(all(0 <= i < n for cells in lay.values() for i in cells.values()))
        self.assertTrue(lay["skin"] and lay["hair"] and lay["eyes"] and lay["outline"])

    def test_every_word_edit_changes_the_picture(self):
        base = built(palette="pico8").doc
        for words in (
            "bigger beard",
            "taller afro",
            "add glasses",
            "close-set eyes",
            "sunglasses",
            "a cap",
            "beanie",
            "goatee",
            "mustache",
            "no beard",
            "bald",
            "short hair",
            "long hair",
            "smile",
            "earrings",
            "big eyes",
            "no brows",
            "lighter hair",
            "darker skin",
            "wider mouth",
            "add a nose",
        ):
            doc = copy.deepcopy(base)
            done = sd.apply_words(doc, words)
            self.assertTrue(done, words)
            self.assertNotEqual(sd.png(doc), sd.png(base), words)

    def test_several_edits_in_one_sentence_and_no_double_reading(self):
        doc = built().doc
        done = sd.apply_words(doc, "bigger beard and add glasses, no hat")
        self.assertEqual(sorted(done), ["a bigger beard", "no hat", "square glasses"])
        self.assertEqual((doc["params"]["glasses"], doc["params"]["hat"]), ("square", "none"))
        doc2 = built().doc
        sd.apply_words(doc2, "remove the glasses")
        self.assertEqual(
            doc2["params"]["glasses"], "none"
        )  # "remove the glasses" is not also "add glasses"

    def test_parameters_are_clamped(self):
        doc = built().doc
        for _ in range(20):
            sd.apply_words(doc, "taller hair")
        self.assertEqual(doc["params"]["hair_height"], sd.LIMITS["hair_height"][1])

    def test_colour_shifts_skip_the_skin_and_the_outline(self):
        doc = built(palette="pico8").doc
        sd.apply_words(doc, "lighter hair")
        self.assertNotIn(doc["params"]["hair_color"], (doc["roles"]["skin"], doc["roles"]["line"]))

    def test_pixel_edits_live_on_their_layer_and_erase_reveals_below(self):
        doc = built(head=16).doc
        (x, y), _ = next(iter(sd.layers(doc)["hair"].items()))
        doc["edits"] = {"hair": {f"{x},{y}": -1}}
        lay = sd.layers(doc)
        self.assertNotIn((x, y), lay["hair"])
        doc["edits"] = {"glasses": {"1,1": 0}}
        self.assertEqual(sd.layers(doc)["glasses"][(1, 1)], 0)

    def test_the_outline_follows_the_body_after_edits(self):
        doc = built(head=16).doc
        before = set(sd.layers(doc)["outline"])
        sd.apply_words(doc, "taller hair")
        self.assertNotEqual(set(sd.layers(doc)["outline"]), before)

    def test_the_file_round_trips_and_bad_files_are_refused(self):
        doc = built(palette="nes").doc
        sd.apply_words(doc, "add glasses and a cap")
        doc["edits"] = {"hat": {"3,3": 1}}
        again = sd.loads(sd.dumps(doc))
        self.assertEqual(sd.png(again), sd.png(doc))
        for bad in (
            "not json",
            json.dumps({"format": "x"}),
            json.dumps({**doc, "palette": "nope"}),
        ):
            with self.assertRaises(sd.DocProblem):
                sd.loads(bad)
        with self.assertRaises(sd.DocProblem):
            sd.loads(json.dumps({**doc, "edits": {"hat": {"999,1": 1}}}))
        with self.assertRaises(sd.DocProblem):
            sd.loads(json.dumps({**doc, "edits": {"hat": {"1,1": 99}}}))
        with self.assertRaises(sd.DocProblem):
            sd.loads(json.dumps({**doc, "edits": {"<script>": {"1,1": 1}}}))

    def test_the_editor_view_has_the_palette_layers_and_edits(self):
        doc = built().doc
        doc["edits"] = {"hair": {"2,2": 1}}
        v = sd.editor_view(doc)
        self.assertEqual(v["palette"], doc["colors"])
        self.assertEqual([lay["name"] for lay in v["layers"]], list(sd.LAYERS))
        self.assertEqual(v["edits"], {"hair": {"2,2": 1}})
        self.assertNotIn(
            [2, 2, 1], next(lay for lay in v["layers"] if lay["name"] == "hair")["cells"]
        )  # layers come before edits


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.mine = self.root / "Pictures"
        self.mine.mkdir()
        self.img, self.face, self.mask = synthetic(beard="full")
        self.img.save(self.mine / "me.png")
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.root / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        charts.set_panel(self.panel, self.files, lambda: 3)
        self.addCleanup(charts.set_panel, None, None)
        sd.STUDIO.__init__()
        for name, value in (
            ("landmarks", (self.face, self.mask)),
            ("detect", [(0.35, 0.27, 0.3, 0.52)]),
        ):
            p = mock.patch.object(art_tool.faces, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        cfg = mock.patch.object(
            source.config, "load_config", return_value={"art": {"folders": [str(self.mine)]}}
        )
        cfg.start()
        self.addCleanup(cfg.stop)
        art_tool.image_style("me", style="sprite", palette="pico8")
        self.card = dict(self.sent[-1])  # a copy: the hub folds later updates into its own card

    def test_a_new_sprite_card_carries_its_id_and_is_editable(self):
        self.assertEqual(pn.validate(self.card), [])
        self.assertTrue(self.card["sprite_id"])
        self.assertFalse(self.card["editing"])
        self.assertEqual(sd.STUDIO.current, self.card["sprite_id"])

    def test_an_edit_updates_the_same_card_in_place_large_sprite(self):
        said = art_tool.sprite_edit("bigger beard and add glasses")
        self.assertIn("Done: a bigger beard, square glasses", said)
        up = self.sent[-1]
        self.assertEqual(pn.validate(up), [])
        self.assertEqual(
            (up["type"], up["target"], up["editing"]), ("panel.update", self.card["panel_id"], True)
        )
        self.assertNotEqual(up["after"], self.card["after"])
        self.assertIn("Glasses: square; earrings off; hat: none", up["settings"])
        self.assertEqual(
            self.panel.lookup(self.card["panel_id"])["editing"], True
        )  # remembered for reconnects

    def test_undo_and_redo(self):
        first = sd.png(sd.STUDIO.get())
        art_tool.sprite_edit("taller hair")
        changed = sd.png(sd.STUDIO.get())
        self.assertEqual(art_tool.sprite_edit("undo"), "Undone.")
        self.assertEqual(sd.png(sd.STUDIO.get()), first)
        self.assertEqual(art_tool.sprite_edit("redo"), "Redone.")
        self.assertEqual(sd.png(sd.STUDIO.get()), changed)
        self.assertEqual(art_tool.sprite_edit("redo"), "Nothing to redo.")

    def test_words_it_does_not_know_list_what_it_can_do_and_leave_no_undo_step(self):
        said = art_tool.sprite_edit("make it purple and dancing")
        self.assertIn("I can change:", said)
        self.assertEqual(art_tool.sprite_edit("undo"), "Nothing to undo.")

    def test_the_card_buttons_are_a_local_shortcut(self):
        sid = self.card["sprite_id"]
        said = commands.handle(f"sprite #{sid}: close-set eyes")
        self.assertIn("closer-set eyes", said)
        self.assertIn("older sprite card", commands.handle("sprite #00000000: bigger beard"))
        self.assertIsNone(
            commands.handle("sprite: bigger beard please and more")
        )  # only the button form, with an id

    def test_pixel_edits_from_the_editor_are_one_undo_step(self):
        sid = self.card["sprite_id"]
        ok, _ = art_tool.sprite_pixels({"hat": {"2,2": 1}}, sid)
        self.assertTrue(ok)
        self.assertEqual(sd.STUDIO.get()["edits"], {"hat": {"2,2": 1}})
        art_tool.sprite_edit("undo")
        self.assertEqual(sd.STUDIO.get()["edits"], {})
        ok, why = art_tool.sprite_pixels({"hat": {"2,2": 999}}, sid)
        self.assertFalse(ok)
        self.assertIn("palette", why)

    def test_save_writes_the_julius_sprite_file_and_it_reopens(self):
        art_tool.sprite_edit("add a cap")
        saved = self.root / "Julius Saved"
        self.assertIn("Saved to", pe.save_card(self.panel, self.files, str(saved)))
        (folder,) = list(saved.iterdir())
        f = folder / f"{folder.name}.julius-sprite"
        self.assertTrue(f.exists())
        self.assertEqual(json.loads(f.read_text())["params"]["hat"], "cap")
        sd.STUDIO.__init__()
        with mock.patch.object(pe, "saved_dir", return_value=saved):
            said = art_tool.sprite_edit("open my sprite")
        self.assertIn("Opened Sprite ", said)
        self.assertEqual(sd.STUDIO.get()["params"]["hat"], "cap")
        self.assertEqual(self.sent[-1]["type"], "panel.art")  # a new card for the reopened sprite

    def test_no_sprite_yet_is_a_sentence(self):
        sd.STUDIO.__init__()
        self.assertIn("no sprite to edit", art_tool.sprite_edit("bigger beard"))

    def test_the_brain_tool_is_registered_with_one_parameter(self):
        import julius.brain.tools as tools

        schema = next(x for x in tools.SCHEMAS if x["function"]["name"] == "sprite_edit")
        self.assertEqual(set(schema["function"]["parameters"]["properties"]), {"change"})
        self.assertIn("Never for making a new sprite", schema["function"]["description"])


class NamingTests(StudioTests):
    def save_as(self, words):
        saved = self.root / "Julius Saved"
        return pe.handle(self.panel, self.files, words, str(saved)), saved

    def test_save_it_as_a_name_and_open_it_by_that_name(self):
        out, saved = self.save_as("Save it as Sunday Roy")
        self.assertIn("Saved to", out)
        self.assertTrue((saved / "Sunday Roy" / "Sunday Roy.julius-sprite").exists())
        sd.STUDIO.__init__()
        with mock.patch.object(pe, "saved_dir", return_value=saved):
            said = commands.handle("Open Sunday Roy")  # the name alone: a saved sprite's exact name
            self.assertIn("Opened Sunday Roy", said)
            self.assertIsNone(commands.handle("Open Safari"))  # not a sprite name: not ours

    def test_names_are_cleaned_and_never_overwrite(self):
        self.save_as("save this sprite as ../../etc/x: <b>")
        self.save_as("save it as Same")
        _, saved = self.save_as("save it as Same")
        names = sorted(p.name for p in saved.iterdir())
        self.assertIn("Same (2)", names)
        self.assertTrue(all("/" not in n and "<" not in n and ".." not in n for n in names), names)

    def test_open_my_sprite_opens_the_newest_and_lists_the_other_recent_ones(self):
        import os
        import time

        saved = self.root / "Julius Saved"
        for i, n in enumerate(("Alpha", "Beta", "Gamma", "Delta")):
            self.save_as(f"save it as {n}")
            f = saved / n / f"{n}.julius-sprite"
            os.utime(f, (time.time() - 100 + i, time.time() - 100 + i))
        with mock.patch.object(pe, "saved_dir", return_value=saved):
            said = art_tool.sprite_edit("open my sprite")
        self.assertIn("Opened Delta", said)
        self.assertIn("Other recent ones: Gamma (", said)
        self.assertIn("Beta (", said)
        self.assertIn("Alpha (", said)

    def test_save_as_with_no_sprite_says_so(self):
        sd.STUDIO.__init__()
        self.panel.cards.clear()
        out, _ = self.save_as("save it as Nothing")
        self.assertIn("don't have a sprite", out)

    def test_every_naming_phrasing_saves_under_the_name(self):
        for words, name in (
            ("Save as Roy's main sprite", "Roy's main sprite"),
            ("save it as Sunday Roy.", "Sunday Roy"),
            ("Save this as Pixel Roy", "Pixel Roy"),
            ("Call it Bob.", "Bob"),
            ("name it Lou, please", "Lou"),
        ):
            out, saved = self.save_as(words)
            self.assertIn(f"in a folder called {name}.", out, words)
            self.assertTrue((saved / name / f"{name}.julius-sprite").exists(), words)
        self.assertIsNone(self.save_as("call it a day")[0])

    def test_the_save_card_tool_saves_with_or_without_a_name(self):
        import julius.brain.tools as tools

        saved = self.root / "Julius Saved"
        schema = next(x for x in tools.SCHEMAS if x["function"]["name"] == "save_card")
        self.assertEqual(set(schema["function"]["parameters"]["properties"]), {"name", "what"})
        self.assertEqual(schema["function"]["parameters"].get("required", []), [])
        with mock.patch.object(pe, "saved_dir", return_value=saved):
            self.assertIn(
                "folder called Roy's main sprite.", tools.save_card(name="Roy's main sprite")
            )
            self.assertIn("Saved to", tools.save_card())
            self.assertIn("Saved to", tools.save_card(what="sprite"))
        self.assertEqual(len(list(saved.iterdir())), 3)


class MainSpriteTests(StudioTests):
    def setUp(self):
        super().setUp()
        self.saved = self.root / "Julius Saved"
        p = mock.patch.object(pe, "saved_dir", return_value=self.saved)
        p.start()
        self.addCleanup(p.stop)

    def test_make_this_my_main_sprite_saves_it_pins_it_and_badges_the_card(self):
        said = commands.handle("Make this my main sprite.")
        self.assertIn("is now your main sprite", said)
        self.assertIn("I saved it first", said)
        name, path = art_tool.main_sprite()
        self.assertTrue(path.exists() and path.suffix == ".julius-sprite")
        pointer = json.loads((self.saved / art_tool.MAIN_FILE).read_text())
        self.assertFalse(Path(pointer["path"]).is_absolute())  # a pointer, not a copy
        self.assertEqual(len(list(self.saved.glob("**/*.julius-sprite"))), 1)
        up = self.sent[-1]
        self.assertEqual(
            (up["type"], up["target"], up["main"]), ("panel.update", self.card["panel_id"], True)
        )
        self.assertEqual(pn.validate(up), [])
        self.assertIn("already your main sprite", commands.handle("set as main sprite"))

    def test_a_saved_sprite_is_pinned_without_a_second_copy_and_a_new_main_replaces_the_old(self):
        pe.handle(self.panel, self.files, "save it as Sunday Roy", str(self.saved))
        first = commands.handle("set it as my main sprite")
        self.assertEqual(first, "Sunday Roy is now your main sprite.")
        art_tool.image_style("me", style="sprite", palette="nes")
        pe.handle(self.panel, self.files, "save as Roy NES", str(self.saved))
        second = commands.handle("make this my main sprite")
        self.assertEqual(second, "Roy NES is now your main sprite. It replaces Sunday Roy.")
        self.assertEqual(len(list(self.saved.glob("**/*.julius-sprite"))), 2)
        off = [m for m in self.sent if m.get("type") == "panel.update" and m.get("main") is False]
        self.assertEqual(off[-1]["target"], self.card["panel_id"])  # the badge left the old card

    def test_open_my_main_sprite_and_the_session_record(self):
        self.assertIn("haven't picked a main sprite", commands.handle("open my main sprite"))
        pe.handle(self.panel, self.files, "save it as Sunday Roy", str(self.saved))
        commands.handle("make this my main sprite")
        art_tool.image_style("me", style="sprite", palette="nes")
        pe.handle(
            self.panel, self.files, "save it as Other main sprite", str(self.saved)
        )  # a name with "main" in it is just a name
        said = commands.handle("Open my main sprite.")
        self.assertIn("Opened your main sprite, Sunday Roy", said)
        self.assertEqual(self.sent[-1]["main"], True)
        from julius.brain import session_state

        self.assertIn("Main sprite: Sunday Roy", session_state.block(""))

    def test_an_edit_after_saving_is_saved_again_before_pinning(self):
        pe.handle(self.panel, self.files, "save it as Sunday Roy", str(self.saved))
        art_tool.sprite_edit("add a cap")
        said = art_tool.sprite_edit("make this my main sprite")
        self.assertIn("I saved it first, as Sunday Roy (2).", said)
        self.assertEqual(json.loads(art_tool.main_sprite()[1].read_text())["params"]["hat"], "cap")

    def test_save_as_main_sprite_means_make_it_main(self):
        self.assertIsNone(pe.handle(self.panel, self.files, "save as main sprite", str(self.saved)))
        self.assertIn("is now your main sprite", commands.handle("save as main sprite"))


class CardOpsTests(StudioTests):
    def test_button_ops_render_at_once_and_update_the_card_in_place(self):
        sid = self.card["sprite_id"]
        before = len(self.sent)
        r = art_tool.sprite_ops(sid, ["bigger beard", "taller hair", "taller hair"])
        self.assertTrue(r["ok"] and r["changed"])
        self.assertEqual((r["undo"], r["redo"]), (3, 0))
        self.assertTrue(r["after"].startswith("/p/"))
        self.assertLess(r["ms"], 100)
        ups = self.sent[before:]
        self.assertEqual(
            [m["type"] for m in ups], ["panel.update"]
        )  # one card update for the batch, no transcript
        self.assertEqual(ups[0]["target"], self.card["panel_id"])

    def test_glasses_toggle_undo_redo_and_limits(self):
        sid = self.card["sprite_id"]
        art_tool.sprite_ops(sid, ["glasses"])
        self.assertEqual(sd.STUDIO.get()["params"]["glasses"], "square")
        art_tool.sprite_ops(sid, ["glasses"])
        self.assertEqual(sd.STUDIO.get()["params"]["glasses"], "none")
        r = art_tool.sprite_ops(sid, ["undo"])
        self.assertEqual(sd.STUDIO.get()["params"]["glasses"], "square")
        self.assertEqual(r["redo"], 1)
        for _ in range(12):
            art_tool.sprite_ops(sid, ["taller hair"])
        r = art_tool.sprite_ops(sid, ["taller hair"])
        self.assertFalse(r["changed"])  # at the limit: no change, no undo step

    def test_unknown_ops_and_old_cards_are_refused(self):
        sid = self.card["sprite_id"]
        self.assertFalse(art_tool.sprite_ops(sid, ["delete my files"])["ok"])
        self.assertFalse(art_tool.sprite_ops(sid, [])["ok"])
        self.assertFalse(art_tool.sprite_ops("00000000", ["undo"])["ok"])

    def test_the_undo_coordinator_undoes_the_sprite_after_card_clicks(self):
        from julius.brain.undo import UndoAgent, UndoCoordinator

        st = sd.STUDIO

        def mark():
            return (st.current, len(st.undo.get(st.current, [])))

        files_ran = []
        c = UndoCoordinator(
            {
                "files": UndoAgent(
                    "the last file change",
                    lambda: 1,
                    lambda: True,
                    lambda: files_ran.append(1) or "moved",
                ),
                "sprite": UndoAgent(
                    "the last sprite edit",
                    mark,
                    lambda: bool(st.undo.get(st.current)),
                    lambda: "back" if art_tool.sprite_edit("undo") == "Undone." else None,
                ),
            }
        )
        art_tool.sprite_ops(self.card["sprite_id"], ["bigger beard"])
        c.observe(4)
        self.assertEqual(c.handle("Undo."), "Undid the last sprite edit. back")
        self.assertEqual(files_ran, [])


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.token = uc.ensure_token(self.dir / "ui_token")
        self.posted = []
        self.typed = []
        self.view = None
        self.ch = uc.Channel(
            self.token,
            {
                "sprite_get": lambda: self.view,
                "sprite_post": lambda edits, sid: (
                    (self.posted.append((edits, sid)) or (True, "saved"))
                    if sid == "ab12"
                    else (False, "That sprite is no longer open.")
                ),
                "sprite_ops": lambda sid, ops: (
                    {"ok": True, "after": "/p/abcdefgh.png", "ops": ops}
                    if sid == "ab12"
                    else {"ok": False, "error": "That sprite is no longer open."}
                ),
                "typed": lambda t: self.typed.append(t),
            },
            audit_path=self.dir / "audit.log",
            port=0,
        ).start()
        self.addCleanup(self.ch.stop)

    def req(self, method, body=None, auth=True, path="/v1/sprite"):
        c = http.client.HTTPConnection("127.0.0.1", self.ch.port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        c.putrequest(method, path, skip_host=True)
        c.putheader("Host", f"127.0.0.1:{self.ch.port}")
        if auth:
            c.putheader("Authorization", "Bearer " + self.token)
        if data is not None:
            c.putheader("Content-Type", "application/json")
            c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        out = json.loads(r.read().decode() or "{}")
        c.close()
        return r.status, out

    def test_get_needs_the_token_and_an_open_sprite(self):
        self.assertEqual(self.req("GET", auth=False)[0], 401)
        self.assertEqual(self.req("GET")[0], 404)
        self.view = {"id": "ab12", "canvas": 32}
        st, out = self.req("GET")
        self.assertEqual((st, out["sprite"]["id"]), (200, "ab12"))

    def test_post_saves_edits_or_says_why_not(self):
        st, _ = self.req("POST", {"id": "ab12", "edits": {"hat": {"1,1": 2}}})
        self.assertEqual(st, 200)
        self.assertEqual(self.posted, [({"hat": {"1,1": 2}}, "ab12")])
        st, out = self.req("POST", {"id": "zz", "edits": {}})
        self.assertEqual((st, out["error"]), (400, "That sprite is no longer open."))


if __name__ == "__main__":
    unittest.main()


class CardEndpointTests(ChannelTests):
    def test_card_edits_answer_with_the_picture_and_never_make_a_turn(self):
        st, out = self.req("POST", {"id": "ab12", "ops": ["bigger beard"]}, path="/v1/sprite/edit")
        self.assertEqual((st, out["after"]), (200, "/p/abcdefgh.png"))
        self.assertEqual(self.typed, [])
        st, out = self.req("POST", {"id": "zz", "ops": ["undo"]}, path="/v1/sprite/edit")
        self.assertEqual(st, 400)
        self.assertEqual(
            self.req("POST", {"id": "ab12", "ops": ["undo"]}, auth=False, path="/v1/sprite/edit")[
                0
            ],
            401,
        )

    def test_fast_clicking_is_not_rate_limited_like_chat(self):
        codes = [
            self.req("POST", {"id": "ab12", "ops": ["taller hair"]}, path="/v1/sprite/edit")[0]
            for _ in range(40)
        ]
        self.assertEqual(set(codes), {200})  # 40 clicks in a few seconds; chat allows 20 per 10 s
