"""Coding tasks as a low-privilege macOS user (threat model S1; plan in julius-orb docs/S1_M3_PLAN.md).

Off unless code.run_as names a user. When it's on:
- a task never quietly runs as you;
- the API key goes to the wrapper on stdin, never in argv;
- Julius keeps what it must trust after the run (task.json, backups, transcript, its git history) where that user
  can't write;
- Julius never runs git on a repo that user could have configured.

The wrappers (scripts/code-run, scripts/code-reap) are tested here as plain functions. The end-to-end test at the
bottom runs only on a Mac where the user and the sudo rule exist (JULIUS_TEST_RUN_AS=julius-code)."""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import julius.agents.code_agent as ca
from tests.test_code_agent import Base

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def script(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class RunAsBase(Base):
    def setUp(self):
        super().setUp()
        self.saved = (ca.RUN_AS, ca.PRIVATE_HOME, ca.CODE_RUN, ca.CODE_REAP, ca.SHARED_ROOT)
        ca.PRIVATE_HOME = self.home / "private"
        ca.RUN_AS = "nobody"  # a real account that isn't you; its home is /var/empty

    def tearDown(self):
        ca.RUN_AS, ca.PRIVATE_HOME, ca.CODE_RUN, ca.CODE_REAP, ca.SHARED_ROOT = self.saved
        super().tearDown()


class Setting(Base):
    def test_off_unless_the_config_names_a_user(self):
        self.assertEqual(ca.RUN_AS, "")
        saved = (ca.RUN_AS, ca.CODE_HOME)
        try:
            ca.configure({"run_as": "julius-code"})
            self.assertEqual(ca.RUN_AS, "julius-code")
            self.assertEqual(ca.CODE_HOME, ca.SHARED_ROOT)
            ca.configure({})
            self.assertEqual(ca.RUN_AS, "")
        finally:
            ca.RUN_AS, ca.CODE_HOME = saved


class NeverAsYou(RunAsBase):
    def test_a_missing_user_stops_the_task_before_it_starts(self):
        ca.RUN_AS = "julius-nosuchuser"
        t = self.task()
        with self.assertRaises(ca.CodeProblem) as cm:
            ca.start(t, api_key="sk-test", config=self.cfg, claude="/bin/echo")
        self.assertEqual(cm.exception.code, "run_as")
        self.assertIn("never runs as you", cm.exception.message)
        self.assertEqual(t.status, "staged")
        self.assertFalse(ca.running())

    def test_each_missing_piece_is_named(self):
        self.assertIn("doesn't exist", ca.run_as_problem("julius-nosuchuser"))
        self.assertIn("not a separate", ca.run_as_problem(os.environ.get("USER") or "root"))
        self.assertIn("not a separate", ca.run_as_problem("root"))
        ca.CODE_RUN = str(self.tmp / "missing")
        self.assertIn("isn't installed", ca.run_as_problem("nobody"))
        mine = self.tmp / "code-run"
        mine.write_text("#!/bin/sh\n")
        ca.CODE_RUN = ca.CODE_REAP = str(
            mine
        )  # owned by you, not root: anything running as you could rewrite it
        self.assertIn("owned by root", ca.run_as_problem("nobody"))


class SudoRuleCheck(RunAsBase):
    def test_the_rule_is_proven_by_running_the_wrapper_not_by_asking_sudo(self):
        """`sudo -l` says yes to any command once one no-password rule exists, so the check runs the wrapper itself."""
        ca.CODE_RUN, ca.CODE_REAP = "/usr/bin/true", "/usr/bin/false"  # root-owned stand-ins
        ca.SHARED_ROOT = self.tmp
        seen = []

        def sudo(answer):
            def run(argv, **kw):
                seen.append(argv)
                self.assertNotIn("-l", argv)
                return subprocess.CompletedProcess(argv, *answer)

            return run

        refused = (1, "", "sudo: a password is required\n")
        with mock.patch.object(ca.subprocess, "run", side_effect=sudo(refused)):
            self.assertIn("sudo rule", ca.run_as_problem("nobody"))
        usage = (2, "", "usage: code-run <task id>\n")
        with mock.patch.object(ca.subprocess, "run", side_effect=sudo(usage)):
            self.assertEqual(ca.run_as_problem("nobody"), "")
        self.assertEqual(seen[-1], [ca.SUDO, "-n", "-u", "nobody", "/usr/bin/false"])


class Launch(RunAsBase):
    def test_the_key_goes_on_stdin_and_the_command_through_one_wrapper(self):
        t = self.task()
        argv, env, _ = ca.build_command(t, "sk-ant-secret-7781", config=self.cfg)
        self.assertEqual(
            argv[3], "/var/empty/.local/bin/claude"
        )  # its own claude: it can't read yours
        self.assertTrue(env["PATH"].startswith("/var/empty/.local/bin:"))
        run_argv, run_env, spec = ca.launch(t, argv, env)
        self.assertEqual(run_argv, [ca.SUDO, "-n", "-u", "nobody", ca.CODE_RUN, t.id])
        self.assertNotIn("sk-ant-secret-7781", " ".join(run_argv) + json.dumps(run_env))
        sent = json.loads(spec)
        self.assertEqual(sent["env"]["ANTHROPIC_API_KEY"], "sk-ant-secret-7781")
        self.assertEqual(sent["argv"], argv)

    def test_it_never_runs_outside_the_sandbox(self):
        t = self.task()
        argv, env, _ = ca.build_command(t, "sk-test", config=self.cfg, sandbox=False)
        with self.assertRaises(ca.CodeProblem):
            ca.launch(t, argv, env)

    def test_without_the_setting_nothing_changes(self):
        ca.RUN_AS = ""
        t = self.task()
        argv, env, _ = ca.build_command(t, "sk-test", config=self.cfg)
        self.assertEqual(ca.launch(t, argv, env), (argv, env, None))


class TrustedRecords(RunAsBase):
    def test_task_records_live_where_the_task_user_cannot_write(self):
        t = self.task()
        self.assertTrue((ca.PRIVATE_HOME / t.id / "task.json").is_file())
        self.assertFalse((t.dir / "task.json").exists())
        # the task user rewrites what it can reach: a fake record pointing the merge at another project
        (t.dir / "task.json").write_text(
            json.dumps(
                {**json.loads((t.private / "task.json").read_text()), "project": "/elsewhere"}
            )
        )
        self.assertEqual(ca.load(t.id).project, t.project)
        self.assertEqual([x.id for x in ca.all_tasks()], [t.id])

    def test_julius_never_runs_git_on_a_repo_the_task_user_configured(self):
        t = self.task()
        mark = self.tmp / "ran-as-you"
        hook = self.tmp / "evil.sh"
        hook.write_text(f"#!/bin/sh\ntouch {mark}\n")
        hook.chmod(0o755)
        with open(t.work / ".git" / "config", "a") as f:
            f.write(f"[core]\n\tfsmonitor = {hook}\n")
        # the attack is real: plain git in that folder runs the task user's program as you
        subprocess.run(
            ["git", "-C", str(t.work), "status"],
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp)},
        )
        self.assertTrue(mark.exists(), "the stand-in attack should work on plain git")
        mark.unlink()
        (t.work / "app.py").write_text("def add(a, b):\n    return a + b\n")
        self.assertEqual(ca.diff_stat(t), (1, 1))
        self.assertTrue(any(ln.startswith("+    return a + b") for ln in ca.diff_lines(t)))
        self.assertFalse(mark.exists())

    def test_merge_skips_a_file_reached_through_a_link(self):
        t = self.task()
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "x.py").write_text("taken from outside the task\n")
        (t.work / "sub").symlink_to(outside)
        with mock.patch.object(
            ca, "changes", return_value={"added": ["sub/x.py"], "modified": [], "deleted": []}
        ):
            plan = ca.merge_plan(t)
        self.assertEqual(plan["apply"], [])
        self.assertEqual(plan["blocked"], ["sub/x.py"])


class MergeKeepsPermissions(RunAsBase):
    def test_a_merge_never_changes_a_project_files_permissions(self):
        """Live 2026-11-27: the merged calc.py came back group-writable (660), copied from the shared task folder."""
        os.chmod(self.proj / "app.py", 0o644)
        t = self.task()  # the copy is opened to the shared group, as in run-as mode
        self.assertEqual((t.work / "app.py").stat().st_mode & 0o777, 0o660)
        (t.work / "app.py").write_text("def add(a, b):\n    return a + b\n")
        (t.work / "new.py").write_text("x = 1\n")
        os.chmod(t.work / "new.py", 0o666)
        ca.merge(t, self.cfg)
        self.assertEqual((self.proj / "app.py").stat().st_mode & 0o777, 0o644)
        self.assertEqual(
            (self.proj / "new.py").stat().st_mode & 0o022, 0
        )  # never group or world writable


class Reaping(RunAsBase):
    def test_reap_and_stop_go_through_the_wrapper_and_end_everything_of_that_user(self):
        t = self.task()
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout='{"killed": [41, 7]}', stderr="")

        with mock.patch.object(ca.subprocess, "run", side_effect=fake_run):
            self.assertEqual(ca._reap(t), [7, 41])
            run = ca.Run(t)
            run.proc = mock.Mock()
            run.proc.poll.return_value = None
            ca._kill(
                run, hard=True
            )  # sudo's process is root's: Julius can't signal it, the wrapper ends the task
        self.assertEqual(calls[0], [ca.SUDO, "-n", "-u", "nobody", ca.CODE_REAP, t.id, "--all"])
        self.assertEqual(len(calls), 2)
        run.proc.wait.assert_called()


class Wrappers(Base):
    def setUp(self):
        super().setUp()
        self.run_mod, self.reap_mod = script("code-run"), script("code-reap")
        self.root = self.tmp / "shared"
        self.folder = self.root / "c0123abcd"
        (self.folder / "work").mkdir(parents=True)

    def spec(self, **env):
        return {
            "argv": [
                "/usr/bin/sandbox-exec",
                "-f",
                str(self.folder / "sandbox.sb"),
                "claude",
                "-p",
                "x",
            ],
            "env": env,
        }

    def test_code_run_takes_a_task_id_and_only_a_sandboxed_command(self):
        plan = self.run_mod.plan
        for bad in ("", "../etc", "c0123abcd/..", "x0123abcd"):
            with self.assertRaises(ValueError):
                plan(bad, self.spec(), root=str(self.root))
        with self.assertRaises(ValueError):
            plan("c99999999", self.spec(), root=str(self.root))  # no such folder
        for argv in (
            ["/bin/sh", "-c", "id"],
            ["/usr/bin/sandbox-exec", "-f", "/tmp/other.sb", "claude"],
        ):
            with self.assertRaises(ValueError):
                plan("c0123abcd", {"argv": argv, "env": {}}, root=str(self.root))
        argv, env, cwd = plan(
            "c0123abcd",
            self.spec(HOME="/Users/roywolley", JULIUS_CODE_TASK="none", ANTHROPIC_API_KEY="k"),
            root=str(self.root),
        )
        self.assertEqual(
            env["HOME"], str(self.folder / "home")
        )  # pinned inside the task folder, whatever was sent
        self.assertEqual(env["JULIUS_CODE_TASK"], "c0123abcd")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "k")
        self.assertEqual(cwd, str(self.folder / "work"))

    def test_code_reap_picks_the_tasks_processes_or_everything_but_itself(self):
        rows = [
            (10, 1, str(self.folder / "work"), "claude"),
            (11, 10, "/", "python JULIUS_CODE_TASK=c0123abcd"),
            (12, 1, "/", "sleep 999"),  # shed both its folder and its marker
            (13, 1, str(self.folder) + "-other", "x"),
            (99, 1, "/", "code-reap"),
        ]
        t = self.reap_mod.targets
        self.assertEqual(t(rows, "c0123abcd", str(self.folder), False, {99}), [10, 11])
        self.assertEqual(t(rows, "c0123abcd", str(self.folder), True, {99}), [10, 11, 12, 13])

    def test_code_reap_opens_the_tasks_files_to_you_without_following_links(self):
        f = self.folder / "work" / "secret_mode.py"
        f.write_text("x")
        f.chmod(0o600)
        exe = self.folder / "work" / "run.sh"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o700)
        d = self.folder / "work" / "made_private"
        d.mkdir(mode=0o700)
        target = self.tmp / "not_the_tasks"
        target.write_text("y")
        target.chmod(0o600)
        (self.folder / "work" / "link").symlink_to(target)
        self.reap_mod.open_up(str(self.folder), os.getuid())
        self.assertEqual(f.stat().st_mode & 0o777, 0o660)
        self.assertEqual(exe.stat().st_mode & 0o777, 0o770)
        self.assertEqual(d.stat().st_mode & 0o777, 0o770)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_both_wrappers_run_on_the_system_python(self):
        for name in ("code-run", "code-reap"):
            text = (SCRIPTS / name).read_text()
            self.assertTrue(text.startswith("#!/usr/bin/python3 -I\n"))
            if Path("/usr/bin/python3").exists():
                r = subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-c",
                        f"import ast; ast.parse(open({str(SCRIPTS / name)!r}).read(), feature_version=(3, 9))",
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(r.returncode, 0, r.stderr)


USER = os.environ.get("JULIUS_TEST_RUN_AS", "")


@unittest.skipUnless(
    USER, "needs the coding user and its sudo rule (JULIUS_TEST_RUN_AS=julius-code)"
)
class EndToEnd(Base):
    """On the real setup: the task runs as the coding user, can't read your things, and leaves nothing running."""

    def setUp(self):
        super().setUp()
        self.saved = (ca.RUN_AS, ca.CODE_HOME, ca.PRIVATE_HOME)
        ca.configure({"run_as": USER})
        ca.PRIVATE_HOME = self.home / "private"
        self.claude = ca.SHARED_ROOT / "_e2e_claude.sh"
        import pwd

        mine = Path(
            pwd.getpwuid(os.getuid()).pw_dir
        )  # the real home: the suite points HOME at a temp folder
        self.claude.write_text(
            "#!/bin/sh\n"
            f"ls {mine} >/dev/null 2>&1 && h=READ || h=denied\n"
            f"cat {mine}/.julius/ui_token >/dev/null 2>&1 && u=READ || u=denied\n"
            f"ls /Users/julius-ci >/dev/null 2>&1 && c=READ || c=denied\n"
            "echo changed > made_by_task.txt\n"
            "(cd / && env -u JULIUS_CODE_TASK /bin/sleep 600 &) \n"
            'echo "{\\"type\\":\\"result\\",\\"subtype\\":\\"success\\",\\"total_cost_usd\\":0,'
            '\\"result\\":\\"who=$(whoami) home=$h token=$u ci=$c\\"}"\n'
        )
        self.claude.chmod(0o750)

    def tearDown(self):
        self.claude.unlink(missing_ok=True)
        ca.RUN_AS, ca.CODE_HOME, ca.PRIVATE_HOME = self.saved
        super().tearDown()

    def test_a_task_runs_as_the_coding_user_and_leaves_nothing_behind(self):
        self.assertEqual(ca.run_as_problem(), "")
        t = ca.create_task("write a file to show the task ran", str(self.proj), config=self.cfg)
        run = ca.start(
            t, api_key="sk-unused", config={**self.cfg, "run_as_claude": str(self.claude)}
        )
        self.assertTrue(run.wait(60))
        self.assertEqual(t.status, "done", t.stop_reason)
        self.assertEqual(t.summary, f"who={USER} home=denied token=denied ci=denied")
        self.assertIn("made_by_task.txt", ca.changes(t)["added"])
        left = subprocess.run(
            ["/bin/ps", "-u", USER, "-o", "pid="], capture_output=True, text=True
        ).stdout.split()
        self.assertEqual(left, [], "the task user still has processes running")
        self.assertTrue(ca.discard(t))


if __name__ == "__main__":
    unittest.main()
