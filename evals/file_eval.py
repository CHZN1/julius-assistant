"""File agent eval: scripted conversations against a throwaway sandbox (a temp folder standing in for the home directory).

Nothing outside the temp folder is read or written. Checks the contract the file agent promises: previews change nothing,
a change happens only after a yes, undo restores, cancel discards, and requests that escape the allowed roots are refused.
No model calls, no cost. Run: python evals/file_eval.py"""
import json, shutil, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).parent))
import common

FIXTURE = {"Downloads": ["budget.csv", "stats.csv", "report.pdf", "notes.txt", "photo.png", "script.py", "data.ipynb"], "Documents": ["resume.txt"], "Desktop": []}


class Env:
    def __init__(self):
        from julius_file_agent import FileAgent
        from julius_file_session import FileSession
        self.dir = Path(tempfile.mkdtemp(prefix="julius_file_eval_")).resolve()
        self.home = self.dir / "home"
        for folder, names in FIXTURE.items():
            (self.home / folder).mkdir(parents=True)
            for n in names: (self.home / folder / n).write_text(f"contents of {n}")
        (self.dir / "outside.txt").write_text("secret")
        self.agent = FileAgent(self.home, self.dir / "state")
        self.session = FileSession(self.agent)
        self.turn = 0

    def say(self, text):
        self.turn += 1
        return self.session.handle(text, self.turn)

    def snapshot(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*") if p.is_file())

    def exists(self, rel): return (self.home / rel).exists()


def msg(out): return (out or {}).get("message", "")


# Each scenario is (id, description, function(env) -> (ok, detail)).
def s_list(e):
    out = e.say("List the files in my Downloads folder.")
    names = [i["name"] for i in (out or {}).get("data", {}).get("items", [])]
    return bool(out and out["ok"] and "budget.csv" in names and len(names) == 7), msg(out)[:80]

def s_find(e):
    out = e.say("Find all my CSV files in Downloads")
    names = sorted(i["name"] for i in (out or {}).get("data", {}).get("items", []))
    return bool(out and out["ok"] and names == ["budget.csv", "stats.csv"]), msg(out)[:80]

def s_read(e):
    out = e.say("Read the file notes.txt in Downloads")
    return bool(out and out["ok"] and "contents of notes.txt" in json.dumps(out.get("data", {})) + msg(out)), msg(out)[:80]

def s_preview_changes_nothing(e):
    before = e.snapshot()
    out = e.say("Rename report.pdf in Downloads to q3_report.pdf")
    return bool(out and out["ok"] and e.snapshot() == before), msg(out)[:80]

def s_rename_needs_yes_then_undo(e):
    e.say("Rename report.pdf in Downloads to q3_report.pdf")
    out = e.say("yes")
    renamed = e.exists("Downloads/q3_report.pdf") and not e.exists("Downloads/report.pdf")
    e.say("undo")
    return bool(out and out["ok"] and renamed and e.exists("Downloads/report.pdf") and not e.exists("Downloads/q3_report.pdf")), msg(out)[:80]

def s_cancel_discards(e):
    before = e.snapshot()
    e.say("Rename report.pdf in Downloads to q3_report.pdf")
    e.say("cancel")
    e.say("yes")
    return e.snapshot() == before, "cancelled plan must not run on a later yes"

def s_move_pattern(e):
    e.say("Move all the CSVs from Downloads into a new folder named Data")
    e.say("yes")
    return e.exists("Downloads/Data/budget.csv") and e.exists("Downloads/Data/stats.csv") and e.exists("Downloads/report.pdf"), "csvs moved, others untouched"

def s_copy_keeps_originals(e):
    e.say("Copy my notebooks from Downloads into Documents")
    e.say("yes")
    return e.exists("Documents/data.ipynb") and e.exists("Downloads/data.ipynb"), "copy leaves the original"

def s_create_file(e):
    e.say('Create a file "Documents/hello.txt" containing "hi there"')
    out = e.say("yes")
    path = e.home / "Documents" / "hello.txt"
    return bool(out and out["ok"] and path.exists() and path.read_text() == "hi there"), msg(out)[:80]

def s_refuse_outside_root(e):
    out = e.say(f"Read the file {e.dir}/outside.txt")
    return not (out and out["ok"] and "secret" in msg(out)), msg(out)[:80]

def s_refuse_parent_escape(e):
    before = e.snapshot()
    e.say("Rename notes.txt to ../../escaped.txt")
    e.say("yes")
    return e.snapshot() == before or not (e.dir / "escaped.txt").exists() and not (e.dir.parent / "escaped.txt").exists(), "rename may not leave the sandbox"

def s_no_delete(e):
    before = e.snapshot()
    out = e.say("Delete everything in my Downloads folder")
    e.say("yes")
    return e.snapshot() == before, msg(out)[:80] or "no delete route"

def s_planning_requests_not_handled(e):
    before = e.snapshot()
    out = e.say("Organize my downloads folder by file type.")
    return out is None and e.snapshot() == before, "organise belongs to the planner, not the file shortcut"

def s_yes_without_plan(e):
    before = e.snapshot()
    e.say("yes")
    return e.snapshot() == before, "a stray yes changes nothing"

def s_collision_not_overwritten(e):
    (e.home / "Downloads" / "taken.txt").write_text("keep me")
    e.say("Rename notes.txt to taken.txt")
    e.say("yes")
    return (e.home / "Downloads" / "taken.txt").read_text() == "keep me", "an existing file is never overwritten"

SCENARIOS = [(f"f{i:02d}", fn.__name__.removeprefix("s_"), fn) for i, fn in enumerate(
    [s_list, s_find, s_read, s_preview_changes_nothing, s_rename_needs_yes_then_undo, s_cancel_discards, s_move_pattern, s_copy_keeps_originals,
     s_create_file, s_refuse_outside_root, s_refuse_parent_escape, s_no_delete, s_planning_requests_not_handled, s_yes_without_plan, s_collision_not_overwritten], 1)]


def main():
    rows = []
    for sid, name, fn in SCENARIOS:
        env = Env()
        t0 = time.perf_counter()
        try: ok, detail = fn(env)
        except Exception as exc: ok, detail = False, f"{type(exc).__name__}: {exc}"[:100]
        rows.append({"id": sid, "cat": "file_agent", "text": name, "ok": bool(ok), "seconds": round(time.perf_counter() - t0, 3), "usd": 0.0, "detail": str(detail)[:100]})
        shutil.rmtree(env.dir, ignore_errors=True)
    n, good = len(rows), sum(r["ok"] for r in rows)
    summary = {"suite": "file", "date": time.strftime("%Y-%m-%d %H:%M"), "cases": n, "ok_pct": round(100 * good / n, 1), "total_usd": 0.0}
    path, previous = common.write_results("file", rows, summary)
    broke, fixed, vs = common.regressions(rows, previous)
    summary["regressions_vs_previous"] = {"previous": vs, "newly_failing": broke, "newly_passing": fixed}
    path.with_suffix(".summary.json").write_text(json.dumps(summary, indent=1))
    print(f"file agent: {good}/{n} scenarios pass ({summary['ok_pct']}%); results {path.name}")
    for r in rows:
        if not r["ok"]: print(f"  FAIL {r['id']} {r['text']}: {r['detail']}")
    if broke: print(f"  REGRESSIONS vs {vs}: {broke}")
    return 1 if broke else 0


if __name__ == "__main__":
    sys.exit(main())
