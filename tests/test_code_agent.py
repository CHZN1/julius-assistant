"""Coding agent sandbox and permission wrapper: project vetting, secret-free copy, no-remote repo, scrubbed command and environment,
a real seatbelt profile, and merge-back that never overwrites unseen edits and never deletes."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import julius.agents.code_agent as ca

SEATBELT = Path("/usr/bin/sandbox-exec").exists() and sys.platform == "darwin"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="julius_code_test_")).resolve()
        self.home = self.tmp / "home"
        self.dev = self.home / "dev"
        self.dev.mkdir(parents=True)
        self.old_home = ca.CODE_HOME
        ca.CODE_HOME = self.home / ".julius" / "code"
        ca.CODE_HOME.mkdir(parents=True)
        self.proj = self.dev / "demo"
        self.proj.mkdir()
        (self.proj / "app.py").write_text("def add(a, b):\n    return a - b\n")
        (self.proj / "test_app.py").write_text("import app\nassert app.add(1, 2) == 3\n")
        (self.proj / "notes.txt").write_text("keep me")
        self.cfg = {"projects_root": str(self.dev)}

    def tearDown(self):
        ca.CODE_HOME = self.old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def task(self, **kw):
        t = ca.create_task(
            "fix the add function so the test passes", str(self.proj), config=self.cfg, **kw
        )
        ca.prepare(t)
        return t


class ProjectVettingTests(Base):
    def test_a_project_directly_under_the_projects_root_is_accepted(self):
        self.assertEqual(ca.check_project(str(self.proj), str(self.dev)), self.proj.resolve())
        self.assertEqual(ca.check_project("demo", str(self.dev)), self.proj.resolve())

    def test_everything_else_is_refused(self):
        (self.dev / "demo" / "inner").mkdir()
        (self.home / "Documents").mkdir()
        for bad, code in (
            (self.dev / "demo" / "inner", "outside_projects"),
            (self.home / "Documents", "outside_projects"),
            (self.dev, "outside_projects"),
            (self.dev / "nope", "no_project"),
            (Path("/"), "outside_projects"),
            (ca.CODE_HOME, "outside_projects"),
        ):
            with self.assertRaises(ca.CodeProblem, msg=str(bad)) as cm:
                ca.check_project(str(bad), str(self.dev))
            self.assertEqual(cm.exception.code, code, str(bad))

    def test_music_and_crypto_projects_are_refused(self):
        for name in (
            "Logic Sessions",
            "GarageBand stuff",
            "btc_bot",
            "crypto_tools",
            "drum_stems",
            "Waves Plugins",
            "music_ideas",
            "ethereum_notes",
        ):
            (self.dev / name).mkdir(exist_ok=True)
            with self.assertRaises(ca.CodeProblem, msg=name) as cm:
                ca.check_project(name, str(self.dev))
            self.assertEqual(cm.exception.code, "protected_project", name)

    def test_a_symlink_out_of_the_projects_root_is_refused(self):
        (self.home / "Documents").mkdir()
        (self.dev / "shortcut").symlink_to(self.home / "Documents")
        with self.assertRaises(ca.CodeProblem):
            ca.check_project("shortcut", str(self.dev))

    def test_cost_cap_is_clamped_and_defaults(self):
        for asked, want in ((None, 1.5), (3, 3.0), (99, 5.0), (0.01, 0.1)):
            self.assertEqual(
                ca.create_task(
                    "fix the add function please", str(self.proj), cap_usd=asked, config=self.cfg
                ).cap_usd,
                want,
            )

    def test_a_vague_goal_is_refused(self):
        with self.assertRaises(ca.CodeProblem):
            ca.create_task("fix", str(self.proj), config=self.cfg)


class CopyTests(Base):
    def test_the_copy_leaves_out_secrets_caches_links_git_and_music(self):
        for rel, text in (
            (".env", "KEY=1"),
            ("config/id_rsa", "k"),
            ("server.pem", "p"),
            ("credentials.json", "{}"),
            (".git/config", "[remote]"),
            ("node_modules/x/index.js", "x"),
            (".venv/bin/python", ""),
            ("mix.wav", "w"),
            ("stems/kick.txt", "k"),
            ("src/ok.py", "print(1)"),
        ):
            p = self.proj / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        (self.proj / "link.txt").symlink_to(self.proj / "notes.txt")
        t = self.task()
        names = set(t.baseline)
        self.assertEqual(names, {"app.py", "test_app.py", "notes.txt", "src/ok.py"})
        self.assertFalse((t.work / ".env").exists())
        self.assertFalse((t.work / "link.txt").exists())

    def test_the_copy_is_a_fresh_repo_with_no_remote_and_a_baseline_commit(self):
        t = self.task()
        self.assertEqual(ca._git(t.work, "remote").stdout.strip(), "")
        self.assertEqual(ca._git(t.work, "log", "--oneline").stdout.split()[1], "baseline")
        self.assertNotIn("remote", (t.work / ".git" / "config").read_text())

    def test_an_oversized_project_is_refused(self):
        old = ca.MAX_FILES
        ca.MAX_FILES = 2
        try:
            with self.assertRaises(ca.CodeProblem) as cm:
                self.task()
            self.assertEqual(cm.exception.code, "too_large")
        finally:
            ca.MAX_FILES = old

    def test_the_real_project_is_untouched_by_preparing(self):
        before = ca.snapshot(self.proj)
        self.task()
        self.assertEqual(ca.snapshot(self.proj), before)


class CommandTests(Base):
    def test_the_command_is_locked_down(self):
        t = self.task()
        argv, env, cwd = ca.build_command(t, "sk-test", config=self.cfg)
        joined = " ".join(argv)
        self.assertEqual(argv[0], "/usr/bin/sandbox-exec")
        self.assertEqual(cwd, t.work)
        for needle in (
            "--bare",
            "dontAsk",
            "--max-budget-usd 1.50",
            "--no-session-persistence",
            "claude-opus-5-5",
            "--strict-mcp-config",
        ):
            self.assertIn(needle, joined)
        for denied in (
            "Bash(git push:*)",
            "Bash(git remote:*)",
            "Bash(curl:*)",
            "Bash(ssh:*)",
            "Bash(rm:*)",
            "Bash(pip:*)",
            "WebFetch",
        ):
            self.assertIn(denied, argv)
        allowed = argv[argv.index("--allowedTools") + 1 : argv.index("--disallowedTools")]
        self.assertFalse([a for a in allowed if "push" in a or "curl" in a or a == "Bash"])
        self.assertNotIn("dangerously", joined)

    def test_the_environment_carries_only_the_one_key_and_private_dirs(self):
        t = self.task()
        fake = {"ODDS_API_KEY": "odds-secret", "AWS_SECRET_ACCESS_KEY": "aws-secret"}
        with patch.dict(os.environ, fake):  # your own values, if any, are back afterwards
            argv, env, _ = ca.build_command(t, "sk-test", config=self.cfg)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-test")
        self.assertNotIn("odds-secret", " ".join(env.values()) + " ".join(argv))
        self.assertNotIn("aws-secret", " ".join(env.values()))
        self.assertTrue(
            Path(env["HOME"]).is_relative_to(t.dir) and Path(env["TMPDIR"]).is_relative_to(t.dir)
        )
        self.assertFalse(
            set(env) & {"ODDS_API_KEY", "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "GITHUB_TOKEN"}
        )

    def test_the_profile_names_the_task_folder_and_denies_personal_folders(self):
        t = self.task()
        text = ca.seatbelt_profile(t, home=self.home)
        self.assertIn(f'(allow file-write* (subpath "{t.dir.resolve()}")', text)
        self.assertIn(f'(deny file-read* (subpath "{self.home}/dev"))', text)
        for p in ("Documents", "Downloads", "Desktop", ".ssh", ".julius", "Music"):
            self.assertIn(f"{self.home}/{p}", text)
        self.assertTrue(
            text.index("(deny file-read*")
            < text.index(f'(allow file-read* (subpath "{t.dir.resolve()}"))')
        )


@unittest.skipUnless(SEATBELT, "needs macOS sandbox-exec")
class SeatbeltTests(Base):
    def run_in(self, t, script):
        (t.dir / "p.sb").write_text(ca.seatbelt_profile(t, home=self.home))
        return subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(t.dir / "p.sb"), "/bin/sh", "-c", script],
            cwd=t.work,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_writes_stay_inside_the_task_folder(self):
        t = self.task()
        self.run_in(t, "true")
        r = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-f",
                str(t.dir / "p.sb"),
                "/bin/sh",
                "-c",
                f'echo a > inside.txt && echo inside-ok; echo b > "{self.proj}/pwn.txt"; echo c > "{self.home}/stray.txt"; echo d > "{t.dir}/home/x.txt" && echo home-ok',
            ],
            cwd=t.work,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertIn("inside-ok", r.stdout)
        self.assertIn("home-ok", r.stdout)
        self.assertFalse((self.proj / "pwn.txt").exists())
        self.assertFalse((self.home / "stray.txt").exists())

    def test_other_projects_and_personal_folders_cannot_be_read(self):
        (self.dev / "other").mkdir()
        (self.dev / "other" / "secret.txt").write_text("TOP-SECRET")
        for d in ("Documents", ".ssh"):
            (self.home / d).mkdir()
            (self.home / d / "private.txt").write_text("PRIVATE")
        t = self.task()
        r = self.run_in(
            t,
            f'cat "{self.dev}/other/secret.txt"; cat "{self.home}/Documents/private.txt"; cat "{self.home}/.ssh/private.txt"; cat app.py && echo own-ok',
        )
        self.assertNotIn("TOP-SECRET", r.stdout)
        self.assertNotIn("PRIVATE", r.stdout)
        self.assertIn("own-ok", r.stdout)

    def test_git_push_has_nowhere_to_go_and_outbound_http_is_blocked(self):
        t = self.task()
        r = self.run_in(t, "git push origin main 2>&1; echo exit=$?")
        self.assertNotIn("exit=0", r.stdout)
        r = self.run_in(
            t,
            "/usr/bin/nc -z -w 3 1.1.1.1 80 >/dev/null 2>&1; echo nc80=$?; /usr/bin/nc -z -w 3 1.1.1.1 22 >/dev/null 2>&1; echo nc22=$?",
        )
        self.assertIn("nc80=1", r.stdout)
        self.assertIn("nc22=1", r.stdout)


class MergeTests(Base):
    def finish(self, t, **files):
        for rel, text in files.items():
            p = t.work / rel
            if text is None:
                p.unlink()
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)

    def test_changes_are_found_and_summarised(self):
        t = self.task()
        self.finish(
            t,
            **{
                "app.py": "def add(a, b):\n    return a + b\n",
                "new.py": "x = 1\n",
                "notes.txt": None,
            },
        )
        c = ca.changes(t)
        self.assertEqual(
            (c["added"], c["modified"], c["deleted"]), (["new.py"], ["app.py"], ["notes.txt"])
        )
        s = ca.summarize(t)
        self.assertIn("1 new and 1 changed", s)
        self.assertIn("deleted 1 file", s)

    def test_merge_applies_added_and_modified_backs_up_and_never_deletes(self):
        t = self.task()
        self.finish(
            t,
            **{
                "app.py": "def add(a, b):\n    return a + b\n",
                "new.py": "x = 1\n",
                "notes.txt": None,
            },
        )
        out = ca.merge(t, self.cfg)
        self.assertEqual(out["modified"], ["app.py"])
        self.assertEqual(out["added"], ["new.py"])
        self.assertIn("a + b", (self.proj / "app.py").read_text())
        self.assertTrue((self.proj / "new.py").exists())
        self.assertEqual((self.proj / "notes.txt").read_text(), "keep me")
        self.assertIn("a - b", (t.dir / "backup" / "app.py").read_text())
        self.assertEqual(ca.load(t.id).status, "merged")

    def test_merge_undo_restores_the_project(self):
        t = self.task()
        before = ca.snapshot(self.proj)
        self.finish(t, **{"app.py": "changed\n", "new.py": "x\n"})
        ca.merge(t, self.cfg)
        self.assertNotEqual(ca.snapshot(self.proj), before)
        ca.undo_merge(t)
        self.assertEqual(ca.snapshot(self.proj), before)

    def test_a_file_edited_in_the_real_project_since_the_copy_is_a_conflict_not_overwritten(self):
        t = self.task()
        self.finish(t, **{"app.py": "agent version\n"})
        (self.proj / "app.py").write_text("my newer edit\n")
        out = ca.merge(t, self.cfg)
        self.assertEqual(out["conflicts"], ["app.py"])
        self.assertEqual((self.proj / "app.py").read_text(), "my newer edit\n")

    def test_a_new_file_that_already_exists_in_the_project_is_a_conflict(self):
        t = self.task()
        self.finish(t, **{"fresh.py": "agent\n"})
        (self.proj / "fresh.py").write_text("mine\n")
        out = ca.merge(t, self.cfg)
        self.assertEqual(out["conflicts"], ["fresh.py"])
        self.assertEqual((self.proj / "fresh.py").read_text(), "mine\n")

    def test_secrets_music_and_git_files_written_by_the_agent_are_never_merged(self):
        t = self.task()
        self.finish(
            t,
            **{
                ".env": "KEY=1",
                "server.pem": "p",
                "mix.wav": "w",
                ".git/hooks/pre-commit": "evil",
                "ok.py": "fine\n",
            },
        )
        out = ca.merge(t, self.cfg)
        self.assertEqual(out["added"], ["ok.py"])
        for n in (".env", "server.pem", "mix.wav"):
            self.assertFalse((self.proj / n).exists(), n)
        self.assertFalse((self.proj / ".git").exists())

    def test_a_key_written_into_code_refuses_the_whole_merge(self):
        # threat model M2: the sandbox's environment holds the API key; a file the agent wrote with it must not merge
        key = "sk-ant-api03-" + "Zk9qLmN4pQ2rS7tV1wX8yB3cD6eF0gH5" + "jK2mN7pQ4rS9tV"
        for name, body in (
            ("config.py", f'API = "{key}"\n'),
            ("deploy.sh", "export AWS_KEY=AKIAIOSFODNN7EXAMPLQ\n"),
            (
                "handoff_notes.md",
                "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n",
            ),  # an innocent name: the contents give it away
        ):
            with self.subTest(name):
                t = self.task()
                self.finish(t, **{name: body, "ok.py": "fine\n"})
                with patch.dict(os.environ, {"ANTHROPIC_API_KEY": key}):
                    plan = ca.merge_plan(t)
                    self.assertEqual([x["file"] for x in plan["secrets"]], [name])
                    self.assertNotIn(key, str(plan))  # the finding names the kind, never the value
                    with self.assertRaises(ca.CodeProblem) as cm:
                        ca.merge(t, self.cfg)
                self.assertEqual(cm.exception.code, "secret")
                self.assertNotIn(key, cm.exception.message)
                self.assertFalse((self.proj / name).exists())
                self.assertFalse((self.proj / "ok.py").exists())  # nothing at all is copied

    def test_test_placeholders_do_not_block_a_merge(self):
        t = self.task()
        self.finish(t, **{"test_x.py": 'KEY = "sk-test"\nTOKEN = "sk-ant-TEST-NOT-A-REAL-KEY"\n'})
        self.assertEqual(ca.merge_plan(t)["secrets"], [])
        self.assertEqual(ca.merge(t, self.cfg)["added"], ["test_x.py"])

    def test_merge_refuses_a_project_that_is_no_longer_eligible(self):
        t = self.task()
        t.project = str(self.home)
        self.finish(t, **{"x.py": "1\n"})
        with self.assertRaises(ca.CodeProblem):
            ca.merge(t, self.cfg)

    def test_discard_only_removes_the_task_folder(self):
        t = self.task()
        ca.discard(t)
        self.assertFalse(t.dir.exists())
        self.assertTrue(self.proj.exists())
        t2 = ca.Task(id="cx", goal="g", project=str(self.proj))
        t2.__class__.dir = property(lambda s: self.proj)
        try:
            with self.assertRaises(ca.CodeProblem):
                ca.discard(t2)
        finally:
            t2.__class__.dir = property(lambda s: ca.CODE_HOME / s.id)
        self.assertTrue(self.proj.exists())


if __name__ == "__main__":
    unittest.main()


class CacheFileTests(Base):
    def test_python_caches_are_not_changes(self):
        """Live regression (18:14): the merge preview listed calc.cpython-312.pyc files the agent's own test run created."""
        before = ca.snapshot(self.proj)
        (self.proj / "__pycache__").mkdir()
        (self.proj / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"x")
        (self.proj / "stray.pyc").write_bytes(b"x")
        self.assertEqual(ca.snapshot(self.proj), before)


class ServiceInterpreterReadTests(unittest.TestCase):
    def test_only_the_interpreter_folder_under_dot_julius_is_readable(self):
        import sys
        from unittest import mock

        home = Path(tempfile.mkdtemp()).resolve()
        env = home / ".julius" / "env-service"
        env.mkdir(parents=True)
        t = mock.Mock()
        t.dir = home / "task"
        with mock.patch.object(sys, "prefix", str(env)):
            text = ca.seatbelt_profile(t, home=home)
        lines = text.splitlines()
        deny = f'(deny file-read* (subpath "{home}/.julius"))'
        allow = f'(allow file-read* (subpath "{env}"))'
        self.assertIn(deny, lines)
        self.assertIn(allow, lines)
        self.assertLess(lines.index(deny), lines.index(allow))
        self.assertNotIn(f'(allow file-read* (subpath "{home}/.julius"))', lines)
        with mock.patch.object(sys, "prefix", "/opt/miniconda3/envs/torch"):
            self.assertNotIn("env-service", ca.seatbelt_profile(t, home=home))
