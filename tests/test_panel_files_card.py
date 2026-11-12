"""Side-panel stage 5: file preview cards (move / copy / rename / trash plans, listings), their status changes and "show more"."""

import tempfile
import unittest
from pathlib import Path

import julius.agents.file_common as fc
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panel_emit as pe
from julius.agents.file_agent import FileAgent
from julius.agents.file_session import FileSession


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = (Path(self.temp.name) / "home").resolve()
        self.docs, self.out = self.root / "Documents", self.root / "Documents" / "Archive"
        for p in (self.docs, self.out, self.root / "Downloads"):
            p.mkdir(parents=True)
        self.agent = FileAgent(self.root, Path(self.temp.name) / "state")
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        fc.set_panel_sink(pe.make_file_sink(self.panel, lambda: 3))
        self.session = FileSession(self.agent)

    def tearDown(self):
        fc.set_panel_sink(None)
        self.temp.cleanup()

    def make(self, n, folder=None, prefix="note"):
        for i in range(n):
            (folder or self.docs).joinpath(f"{prefix}{i:02d}.txt").write_text("x" * (i + 1))

    def cards(self):
        return [m for m in self.sent if m["type"] == "panel.files"]

    def updates(self):
        return [m for m in self.sent if m["type"] == "panel.update"]


class PlanCards(Base):
    def test_move_preview_becomes_a_card_then_completed_then_undone(self):
        self.make(3)
        r = self.session.handle('Move text files named "note*.txt" from Documents to Archive', 1)
        self.assertEqual(r["code"], "preview", r)
        card = self.cards()[0]
        self.assertEqual(
            (card["kind"], card["action"], card["status"]), ("plan", "move", "pending")
        )
        self.assertEqual(card["title"], "Move 3 files")
        self.assertEqual(card["turn"], 3)
        self.assertEqual(
            [i["name"] for i in card["items"]], ["note00.txt", "note01.txt", "note02.txt"]
        )
        self.assertEqual(card["items"][2]["size"], 3)
        self.assertIn("→", card["where"])
        self.assertEqual(pn.validate(card), [])
        self.session.handle("yes", 2)
        self.assertEqual(self.updates()[-1]["status"], "completed")
        self.assertEqual(self.panel.lookup()["status"], "completed")  # a reconnect sees the same
        self.agent.undo()
        self.assertEqual(self.updates()[-1]["status"], "undone")

    def test_cancel_marks_the_pending_card_and_a_new_plan_expires_the_old_one(self):
        self.make(2)
        self.session.handle('Move text files named "note*.txt" from Documents to Archive', 1)
        self.session.handle("cancel", 2)
        self.assertEqual(self.updates()[-1]["status"], "cancelled")
        self.session.handle('Copy text files named "note*.txt" from Documents to Archive', 3)
        self.session.handle('Move text files named "note00.txt" from Documents to Archive', 4)
        # asking for something new while a plan waits cancels that plan (the agent says so itself); the card follows
        self.assertEqual(
            [c["status"] for c in self.panel.cards.values()], ["cancelled", "cancelled", "pending"]
        )
        self.assertEqual(sum(c["status"] == "pending" for c in self.panel.cards.values()), 1)

    def test_trash_and_rename_cards(self):
        self.make(2)
        res = self.agent.propose("trash", source="Documents", pattern="note0*.txt", turn=1)
        self.assertEqual(res["code"], "preview", res)
        c = self.cards()[-1]
        self.assertEqual((c["action"], c["title"]), ("trash", "Move 2 files to the trash"))
        self.assertEqual(sorted(i["name"] for i in c["items"]), ["note00.txt", "note01.txt"])
        res = self.agent.propose(
            "rename", path="Documents/note00.txt", destination="Documents/first.txt", turn=5
        )
        self.assertEqual(res["code"], "preview")
        card = self.cards()[-1]
        self.assertEqual(
            (card["action"], card["items"][0]["name"], card["items"][0]["was"]),
            ("rename", "first.txt", "note00.txt"),
        )

    def test_hostile_names_are_cleaned_and_a_broken_sink_never_costs_the_reply(self):
        plan = {
            "id": "p1",
            "action": "create",
            "status": "pending",
            "total_bytes": 2,
            "ops": [
                {"from": None, "to": str(self.docs / "evil‮txt.exe"), "fingerprint": {"size": 2}}
            ],
        }
        card = pn.files_card_from_plan(plan, "hi\x07 there")
        self.assertNotIn("‮", card["all_items"][0]["name"])
        self.assertNotIn("\x07", card["summary"])
        fc.set_panel_sink(lambda r: 1 / 0)
        self.assertEqual(self.agent.list("Documents")["code"], "listed")


class ShowMore(Base):
    def test_a_long_plan_shows_25_rows_and_show_more_reveals_the_rest_in_steps(self):
        self.make(60)
        self.session.handle('Move text files named "note*.txt" from Documents to Archive', 1)
        card = self.cards()[0]
        self.assertEqual((card["total"], card["shown"], card["more"]), (60, 25, True))
        self.assertEqual(len(card["items"]), 25)
        self.assertEqual(
            pe.handle(self.panel, None, "show more"), "Showing 25 more. 10 still to show."
        )
        self.assertEqual(self.updates()[-1]["shown"], 50)
        self.assertEqual(pe.handle(self.panel, None, "Show me the rest."), "Showing 10 more.")
        up = self.updates()[-1]
        self.assertEqual((up["shown"], up["more"], up["items"][-1]["n"]), (60, False, 60))
        self.assertEqual(len(self.panel.lookup()["items"]), 60)
        self.assertEqual(pe.handle(self.panel, None, "show more"), "That's everything on the card.")
        for m in self.sent:
            self.assertEqual(pn.validate(m), [], m)

    def test_listing_card_asks_the_agent_for_a_longer_listing(self):
        self.make(100, self.root / "Downloads", prefix="d")
        self.agent.list("Downloads", limit=40)
        card = self.cards()[0]
        self.assertEqual((card["kind"], card["total"], card["more"]), ("listing", 40, True))
        self.assertTrue(
            pe.handle(self.panel, None, "show more").startswith("Showing 15 more")
        )  # 25 of the 40 were hidden, now 40 shown
        self.assertEqual(self.panel.lookup()["shown"], 40)
        said = pe.show_more(
            self.panel, lister=lambda path, limit: self.agent.list(path, limit=limit)
        )
        self.assertEqual(said, "Showing 40 more.")
        self.assertEqual(self.panel.lookup()["shown"], 80)
        self.assertTrue(self.panel.lookup()["more"])
        pe.show_more(self.panel, lister=lambda path, limit: self.agent.list(path, limit=limit))
        self.assertEqual(self.panel.lookup()["shown"], 100)
        self.assertFalse(self.panel.lookup()["more"])

    def test_show_more_with_no_file_card_says_there_is_nothing_to_show(self):
        nothing = "There's nothing to show more of right now."
        self.assertEqual(pe.handle(self.panel, None, "show more"), nothing)
        self.panel.research(
            {"question": "q", "summary": "s", "findings": [], "sources": [], "images": []}
        )
        self.assertEqual(pe.handle(self.panel, None, "Show more."), nothing)
        self.assertEqual(pe.handle(self.panel, None, "show me the rest"), nothing)
        self.assertEqual(
            [m["type"] for m in self.sent], ["panel.research"]
        )  # nothing was published
        for t in ("show more cheese", "tell me more", "show"):
            self.assertIsNone(pe.handle(self.panel, None, t))


if __name__ == "__main__":
    unittest.main()
