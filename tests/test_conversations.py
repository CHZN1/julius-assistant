"""Conversations Half 1 (backend): the store, recording and switching, what the brain sees, the voice/typed commands, the conversations tool,
and the migration of the old session logs (dry run by default, copy only, verified)."""

import importlib.util
import io
import json
import os
import stat
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from julius.conversations import commands as cc
from julius.conversations.manager import Conversations
from julius.conversations.store import ConversationStore, fts_query
from tests.harness import make_session


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = ConversationStore(self.root / "c.sqlite3")
        self.addCleanup(self.store.close)
        self.llm_calls = []

        def llm(system, text, n):
            self.llm_calls.append((system, text))
            if "title" in system:
                return "Sprite size talk"
            if "summary" in system.lower():
                return "Alex and Julius settled on a 24 px Game Boy sprite."
            return "We talked about the sprite size."

        self.conv = Conversations(self.store, llm=llm, background=False)
        self.history = [{"role": "system", "content": "sys"}]

    def say(self, text, answer="ok."):
        out = cc.handle(self.conv, text)
        self.conv.after_turn(self.history, text, out if out is not None else answer)
        if len(self.history) > 9:  # what finish_turn does next
            self.history[:] = [self.history[0]] + self.history[-8:]
        return out


class StoreTests(Base):
    def test_the_file_is_private_and_in_wal_mode(self):
        self.assertEqual(stat.S_IMODE(os.stat(self.root / "c.sqlite3").st_mode), 0o600)
        self.assertEqual(self.store._q("PRAGMA journal_mode")[0][0], "wal")

    def test_messages_are_the_last_n_starting_with_his(self):
        cid = self.store.new()
        for i in range(6):
            self.store.add(cid, "user", f"q{i}")
            self.store.add(cid, "assistant", f"a{i}")
        msgs = self.store.messages(cid, 7)
        self.assertEqual(msgs[0], {"role": "user", "content": "q3"})
        self.assertEqual(len(msgs), 6)

    def test_search_ranks_titles_and_lines_and_survives_odd_words(self):
        a = self.store.new("Sprite editor")
        b = self.store.new("Drum loops")
        self.store.add(b, "user", "which sprites go with the drum video?")
        hits = self.store.search("the sprites")
        self.assertEqual([h["id"] for h in hits][:2], [a, b])
        self.assertEqual(self.store.search('"; DROP TABLE turns; -- NEAR( OR'), [])
        self.assertEqual(fts_query("what were we talking about"), "")

    def test_his_rename_wins_over_an_automatic_title(self):
        cid = self.store.new()
        self.assertTrue(self.store.rename(cid, "Mine"))
        self.assertFalse(self.store.rename(cid, "Auto", auto=True))
        self.assertEqual(self.store.get(cid)["title"], "Mine")

    def test_delete_is_the_trash_and_restorable(self):
        cid = self.store.new("Gone")
        self.store.trash(cid)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.list(trash=True)[0]["id"], cid)
        self.store.restore(cid)
        self.assertEqual(self.store.list()[0]["id"], cid)
        self.assertEqual(self.store.count(cid), 0)  # nothing was removed


class RecordingTests(Base):
    def test_turns_are_recorded_titled_after_three_and_summarised_past_sixteen(self):
        for i in range(3):
            self.say(f"How big should sprite {i} be?")
        c = self.conv.info()
        self.assertEqual((c["n"], c["title"]), (6, "Sprite size talk"))
        for i in range(6):
            self.say(f"more {i}")
        s = self.store.summary(c["id"])  # 18 messages: past 16, so the first summary is made
        self.assertIsNotNone(s)
        self.assertIn("24 px", s["text"])
        self.assertIn("24 px", self.conv.context_block())
        n = len(self.llm_calls)
        for i in range(5):
            self.say(f"later {i}")
        self.assertEqual(len(self.llm_calls), n)  # 10 new messages: no refresh yet (every 12)
        self.say("later 5")
        self.assertEqual(len(self.llm_calls), n + 1)

    def test_conversation_commands_are_not_recorded(self):
        self.say("hello")
        self.say("pin this conversation")
        self.assertEqual(self.conv.info()["n"], 2)

    def test_a_switch_waits_for_the_end_of_the_turn_and_loads_only_that_conversation(self):
        self.say("Let's plan the sprite editor")
        first = self.conv.current
        self.store.rename(first, "Sprite editor")
        out = cc.handle(self.conv, "New conversation about drum loops")
        self.assertEqual(self.conv.current, first)  # not under the turn
        self.conv.after_turn(self.history, "New conversation about drum loops", out)
        self.assertNotEqual(self.conv.current, first)
        self.assertEqual(self.history, [{"role": "system", "content": "sys"}])
        self.say("What tempo for dub?", "About 70 BPM.")
        self.say("Go back to the conversation about the sprite editor")
        self.assertEqual(self.conv.current, first)
        self.assertEqual(
            [m["content"] for m in self.history[1:]], ["Let's plan the sprite editor", "ok."]
        )

    def test_a_restart_carries_on_a_recent_conversation_only(self):
        self.say("remember the sprite size")
        h = [{"role": "system", "content": "sys"}]
        again = Conversations(self.store, llm=None, background=False)
        self.assertTrue(again.resume(h))
        self.assertEqual(len(h), 3)
        later = Conversations(self.store, llm=None, background=False)
        h2 = [{"role": "system", "content": "sys"}]
        self.assertIsNone(later.resume(h2, now=time.time() + 13 * 3600))
        self.assertEqual(len(h2), 1)


class CommandTests(Base):
    def setUp(self):
        super().setUp()
        self.say("How big should the sprite be?")
        self.sprite = self.conv.current
        self.store.rename(self.sprite, "Sprite editor")
        self.say("New conversation about drum loops")
        self.say("Which tempo for dub drums?")
        self.drums = self.conv.current

    def test_the_commands(self):
        self.assertEqual(
            self.say("Move this to the Music project"),
            "No project is close to Music, so I created a new project, Music, and moved drum loops into it.",
        )
        self.assertEqual(
            self.say("Rename this conversation to Dub tempo"),
            "Renamed this conversation to Dub tempo.",
        )
        self.assertEqual(self.say("Pin this conversation."), "Pinned Dub tempo.")
        self.assertIn("pinned: Dub tempo", self.say("List my conversations"))
        self.assertIn("in the Music project", self.say("Which conversation is this?"))
        recap = self.say("What were we talking about in the sprite conversation?")
        self.assertIn("In Sprite editor", recap)
        self.assertEqual(self.conv.current, self.drums)  # a recap never switches
        self.assertIn("Switched to Sprite editor", self.say("Open the conversation about sprites"))
        self.assertIn("Switched to Dub tempo", self.say("Switch to my Music project"))
        self.assertIn(
            "don't have a project called banana", self.say("switch to the banana project")
        )

    def test_delete_asks_first_then_trash_then_restore(self):
        q = self.say("Delete this conversation")
        self.assertIn("Say yes to delete it", q)
        self.assertIsNone(self.store.get(self.drums)["deleted_at"])
        self.assertIn("Moved drum loops to the trash", self.say("yes"))
        self.assertIsNotNone(self.store.get(self.drums)["deleted_at"])
        self.assertIsNone(self.conv.current)  # the next turn starts a fresh one
        self.assertIn("Restored drum loops", self.say("Restore my last deleted conversation"))
        self.say("A new question")  # a fresh conversation starts
        self.assertIn("Say yes", self.say("Delete this conversation"))
        self.assertEqual(self.say("no"), "Kept it.")

    def test_two_close_matches_get_one_question_then_the_answer_opens_one(self):
        for title in ("Sprite colours", "Sprite sizes"):
            cid = self.store.new(title)
            self.store.add(cid, "user", "about the sprite")
        q = self.say("open the conversation about sprite")
        self.assertIn("Which one?", q)
        self.assertIn("Switched to", self.say("the first one"))

    def test_notes_by_scope_and_what_the_brain_gets(self):
        self.say("Move this to the Music project")
        self.say("Note for this chat: we picked 72 BPM")
        self.say("Remember for this project that the drums are in the Loops folder")
        block = self.conv.notes_block("which BPM and where are the drums?")
        self.assertIn("(this chat) we picked 72 BPM", block)
        self.assertIn("(Music project) the drums are in the Loops folder", block)
        self.say("Go back to the conversation about the sprite editor")
        self.assertEqual(
            self.conv.notes_block("which BPM and where are the drums?"), ""
        )  # never another conversation's notes

    def test_moving_matches_a_close_project_before_creating_one(self):
        self.store.project_id("Julius")
        self.assertEqual(
            self.say("Move this to the Julias project"), "Moved drum loops to the Julius project."
        )
        self.assertIn("created a new project, Hockey", self.say("move this to the hockey project"))
        self.assertEqual(len(self.store.projects()), 3)  # Inbox, Julius, Hockey: no "Julias"

    def test_a_recap_says_plainly_when_the_last_question_was_cut_off_and_answers_it_on_yes(self):
        from julius.runtime import turn as rt

        self.say("Go back to the conversation about the sprite editor")
        self.conv.after_turn(
            self.history,
            "What palette should the editor default to?",
            "Response stopped by user.",
            {"cancelled": True},
        )
        self.say("New conversation")
        out = self.say("What were we talking about in the sprite editor conversation?")
        self.assertIn(
            "you asked “What palette should the editor default to?” and it got cut off before I answered. Want me to answer it now?",
            out,
        )
        self.assertNotIn(
            "palette", self.llm_calls[-1][1]
        )  # the recap model never sees the unanswered exchange
        s = make_session(conversations=self.conv)
        s.history = list(self.history)
        t = rt.Turn("Yes.", "en", 0.0)
        with mock.patch.object(rt, "_command_reply", return_value=None):
            rt.route(s, t)
        self.assertEqual(t.text, "What palette should the editor default to?")
        self.assertEqual(self.conv.current, self.sprite)  # answered in its own conversation
        self.assertEqual(
            [m["content"] for m in s.history[1:]], ["How big should the sprite be?", "ok."]
        )

    def test_a_recap_offer_lapses_on_anything_but_yes(self):
        self.conv.after_turn(self.history, "And the tempo?", "[interrupted by the user]")
        self.say("New conversation")
        self.assertIn(
            "got cut off", self.say("What were we talking about in the dub drums conversation?")
        )
        self.assertIsNone(
            self.conv.take_replay("never mind, what time is it", self.history)
        )  # the next turn, as route() sees it
        self.assertIsNone(self.conv.take_replay("yes", self.history))

    def test_ordinary_requests_are_left_alone(self):
        for t in (
            "remember that I like jazz",
            "open my sprite",
            "what time is it",
            "save this card",
            "make me a sprite",
            "delete the file notes.txt",
        ):
            self.assertIsNone(cc.handle(self.conv, t), t)

    def test_the_brain_tool_does_the_same(self):
        from julius.brain import tools

        with mock.patch.object(tools, "conversations_manager", self.conv):
            self.assertIn(
                "Switched to Sprite editor", tools.conversations(action="switch", query="sprite")
            )
            self.assertIn("Say yes", tools.conversations(action="delete"))
            self.assertIn("In Sprite editor", tools.conversations(action="recap", query="sprite"))
        schema = next(x for x in tools.SCHEMAS if x["function"]["name"] == "conversations")
        self.assertEqual(schema["function"]["parameters"]["required"], ["action"])
        self.assertIn(
            "conversations",
            [
                s["function"]["name"]
                for s in tools.select_schemas("go back to yesterday's chat about the drum loops")
            ],
        )


class SidebarTests(Base):
    def setUp(self):
        super().setUp()
        import julius.ui_bridge.events as ev
        import julius.ui_bridge.panel as pn

        self.pn = pn
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/t")
        self.panel.conv_fn = self.conv.ensure
        self.conv.on_change = self.panel.conversations
        self.say("How big should the sprite be?")
        self.a = self.conv.current
        self.store.rename(self.a, "Sprite editor")
        self.say("New conversation about drums")
        self.say("Which tempo?")
        self.b = self.conv.current

    def op(self, **body):
        from julius.conversations import channel

        return channel.op(self.conv, body, busy=False, history=self.history)

    def test_the_feed_lists_conversations_and_validates(self):
        msg = [m for m in self.sent if m["type"] == "panel.conversations"][-1]
        self.assertEqual(self.pn.validate(msg), [])
        self.assertEqual(msg["current"], self.b)
        self.assertEqual({c["id"] for c in msg["conversations"]}, {self.a, self.b})
        self.assertEqual(self.panel.snapshot()["conversations"]["current"], self.b)
        self.assertEqual(
            msg["messages"],
            [{"who": "you", "text": "Which tempo?"}, {"who": "julius", "text": "ok."}],
        )  # the transcript beside the orb, when this one is opened

    def test_sidebar_actions_by_id(self):
        r = self.op(action="switch", id=self.a)
        self.assertEqual((r["ok"], r["state"]["current"]), (True, self.a))
        self.assertEqual(self.history[1]["content"], "How big should the sprite be?")
        self.assertTrue(self.op(action="pin", id=self.b, on=True)["ok"])
        self.assertTrue(self.store.get(self.b)["pinned"])
        self.assertTrue(self.op(action="rename", id=self.b, title="Dub drums")["ok"])
        self.assertIn(
            "created a new project", self.op(action="move", id=self.b, project="music")["said"]
        )
        self.assertEqual(
            self.op(action="move", id=self.a, project="Musik")["said"],
            "Moved Sprite editor to the Music project.",
        )
        self.assertFalse(self.op(action="delete", id=self.b)["ok"])  # the sheet confirms first
        self.assertTrue(self.op(action="delete", id=self.b, confirm=True)["ok"])
        self.assertEqual(self.op(action="trash")["results"][0]["id"], self.b)
        self.assertTrue(self.op(action="restore", id=self.b)["ok"])
        hits = self.op(action="search", query="sprite")["results"]
        self.assertEqual(hits[0]["id"], self.a)
        self.assertFalse(self.op(action="switch", id=999)["ok"])
        self.assertFalse(self.op(action="drop_tables")["ok"])

    def test_a_switch_while_answering_waits_for_the_turn(self):
        from julius.conversations import channel

        r = channel.op(
            self.conv, {"action": "switch", "id": self.a}, busy=True, history=self.history
        )
        self.assertEqual((r["said"], self.conv.current), ("after this answer", self.b))
        self.conv.after_turn(self.history, "and the swing?", "Light swing.")
        self.assertEqual(self.conv.current, self.a)
        self.assertEqual(
            self.store.messages(self.b)[-1]["content"], "Light swing."
        )  # the answer stayed where it was asked

    def test_cards_carry_their_conversation_and_are_kept_per_conversation(self):
        for i in range(25):
            self.panel.table({"title": f"t{i}", "unit": "", "columns": ["a"], "rows": [["1"]]})
        self.assertTrue(
            all(m.get("conv") == self.b for m in self.sent if m["type"] == "panel.table")
        )
        self.op(action="switch", id=self.a)
        self.panel.table({"title": "sprite table", "unit": "", "columns": ["a"], "rows": [["1"]]})
        kept = list(self.panel.cards.values())
        self.assertEqual(sum(1 for c in kept if c["conv"] == self.b), 20)
        self.assertEqual(sum(1 for c in kept if c["conv"] == self.a), 1)

    def test_memory_used_lists_the_notes_with_their_scope(self):
        self.say("Note for this chat: we picked 72 BPM")
        items = self.conv.recall_items("which BPM did we pick")
        self.assertEqual(items[0]["scope"], "conversation")
        self.assertTrue(self.panel.memory_used(items, turn=3))
        self.assertEqual(self.pn.validate(self.sent[-1]), [])
        r = self.op(action="forget_note", id=items[0]["id"])
        self.assertTrue(r["ok"])
        self.assertEqual(self.conv.recall_items("which BPM did we pick"), [])


class RuntimeTests(Base):
    def test_finish_turn_records_and_the_brain_request_carries_the_state(self):
        from julius.runtime import turn as rt
        from julius.runtime.brain_turn import BrainTurn

        s = make_session(conversations=self.conv)
        s.history = list(self.history)
        t = rt.Turn("What size for the sprite?", "en", time.perf_counter())
        t.reply = "24 pixels."
        s.history += [
            {"role": "user", "content": t.text},
            {"role": "assistant", "content": t.reply},
        ]
        rt.finish_turn(s, t)
        self.assertEqual(self.conv.info()["n"], 2)
        bt = BrainTurn.__new__(BrainTurn)
        bt.s, bt.user_text, bt.tool_carry, bt.nudge = s, "and the palette?", False, False
        s.cloud_brain = mock.Mock()  # the cloud path keeps the system messages as they are
        bt.build_messages("")
        systems = [m["content"] for m in bt.messages if m["role"] == "system"]
        self.assertTrue(any("SESSION STATE (this conversation" in x for x in systems), systems)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "mig", Path(__file__).resolve().parents[1] / "scripts" / "migrate_conversations.py"
        )
        self.mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mig)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        mem = root / "julius_memory"
        (mem / "sessions").mkdir(parents=True)
        for day, n in (("2026-11-05", 2), ("2026-11-06", 3)):
            with open(mem / "sessions" / f"{day}.jsonl", "w") as f:
                for i in range(n):
                    f.write(
                        json.dumps(
                            {
                                "t": f"{day}T10:0{i}:00",
                                "user": f"question {i} é",
                                "julius": f"answer {i}",
                            }
                        )
                        + "\n"
                    )
                f.write("not json\n")
        (mem / "projects.json").write_text('{"projects": []}')
        (mem / "last_session.json").write_text(
            '{"date": "2026-11-06", "summary": "Tokyo time and NHL scores."}'
        )
        self.before = {p.name: p.read_bytes() for p in (mem / "sessions").iterdir()}
        self.mem, self.db, self.log = mem, root / "conv.sqlite3", root / "MOVE_LOG.md"
        for name, value in (("MEM", mem), ("MOVE_LOG", self.log)):
            p = mock.patch.object(self.mig, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_tool(self, *args):
        buf = io.StringIO()
        with (
            mock.patch("sys.argv", ["x", "--db", str(self.db), *args]),
            redirect_stdout(buf),
            mock.patch.object(self.mig, "haiku_title", lambda rows: "Tokyo and hockey"),
        ):
            code = self.mig.main()
        return code, buf.getvalue()

    def test_the_dry_run_writes_nothing(self):
        code, out = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertIn("To create: 2 conversations, 10 turns", out)
        self.assertIn("1 unreadable line(s) skipped", out)
        self.assertFalse(self.db.exists())
        self.assertFalse(self.log.exists())

    def test_the_real_run_needs_yes_copies_verifies_logs_and_is_idempotent(self):
        self.assertEqual(self.run_tool("--apply")[0], 2)
        self.assertFalse(self.db.exists())
        code, out = self.run_tool("--apply", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("Verified: every file matches.", out)
        st = ConversationStore(self.db)
        rows = st.list()
        self.assertEqual(
            sorted(r["title"] for r in rows),
            ["2026-11-05: Tokyo and hockey", "2026-11-06: Tokyo and hockey"],
        )
        newest = next(r for r in rows if r["title"].startswith("2026-11-06"))
        self.assertEqual(st.summary(newest["id"])["text"], "Tokyo time and NHL scores.")
        st.close()
        self.assertIn("Copy only", self.log.read_text())
        self.assertEqual(
            {p.name: p.read_bytes() for p in (self.mem / "sessions").iterdir()}, self.before
        )  # originals untouched
        code, out = self.run_tool("--apply", "--yes")
        self.assertIn("To create: 0 conversations", out)


if __name__ == "__main__":
    unittest.main()


class ProjectRecapTests(unittest.TestCase):
    """ "What did we decide in the Julius project?" recaps that project's notes, not whichever conversation mentions the words."""

    def test_project_notes(self):
        import tempfile

        from julius.conversations.manager import Conversations
        from julius.conversations.store import ConversationStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = ConversationStore(Path(tmp.name) / "c.sqlite3")
        conv = Conversations(store)
        pid = store.project_id("Julius")
        store.add_memory("project", pid, "Decided: the panel docks on the right.")
        store.add_memory("project", pid, "Decided: the demo is 9:16.")
        cur = store.new("Julius project folder talk")  # a conversation whose words match better
        store.add(cur, "user", "which project folder for the Julius project?")
        said = conv.recap("Julius project")
        self.assertTrue(
            said.startswith(
                "In the Julius project you decided: the panel docks on the right; the demo is 9:16"
            ),
            said,
        )
