"""Coding-agent sandbox escape tests (Security stage part 3).

Every attempt runs inside the real seatbelt profile (and, for processes, the real task runner with a scripted stand-in for
`claude`), against a fake home that mirrors the real one, so nothing here touches your own files. Each attempt must be
blocked; the merge-back checks must refuse what would carry something out of the copy."""

import contextlib
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

import julius.agents.code_agent as ca
from julius.agents import egress_proxy as ep

SANDBOX = os.path.exists("/usr/bin/sandbox-exec")


@unittest.skipUnless(SANDBOX, "needs macOS sandbox-exec")
class Escape(unittest.TestCase):
    def setUp(self):
        from tests.test_code_agent import Base

        self.b = Base()
        self.b.setUp()
        self.addCleanup(self.b.tearDown)
        self.home = self.b.home
        self.secrets = {  # what a real home holds, mirrored in the fake one
            ".ssh/id_ed25519": "PRIVATE KEY",
            ".aws/credentials": "aws_secret_access_key = x",
            ".zsh_history": "export ODDS_API_KEY=abc123",
            ".bash_history": "export TOKEN=abc",
            ".zshrc": "export OPENAI_API_KEY=sk-x",
            ".julius/ui_token": "token",
            ".netrc": "machine github.com password x",
            "Documents/taxes.pdf": "taxes",
            "dev/other/.env": "KEY=1",
            "Library/Application Support/Firefox/Profiles/x/logins.json": "{}",
            "Library/Cookies/Cookies.binarycookies": "c",
            "Library/Containers/com.apple.Notes/Data/notes": "n",
            "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite": "n",
        }
        for rel, body in self.secrets.items():
            p = self.home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        self.p = ep.EgressProxy().start()
        self.addCleanup(self.p.stop)
        # where production keeps coding tasks (not under ~/.julius, which the sandbox denies as a whole)
        ca.CODE_HOME = self.home / "Library" / "Application Support" / "Julius" / "code"
        ca.CODE_HOME.mkdir(parents=True, exist_ok=True)
        self.t = self.b.task()
        self.profile = self.t.dir / "p.sb"
        self.write_profile()

    def write_profile(self):
        self.profile.write_text(ca.seatbelt_profile(self.t, home=self.home, proxy_port=self.p.port))

    def in_sandbox(self, argv, timeout=30):
        """Run `argv` inside the task's seatbelt profile, in its copy."""
        return subprocess.run(
            ["/usr/bin/sandbox-exec", "-f", str(self.profile), *argv],
            cwd=self.t.work,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def sandboxed(self, code, timeout=30):
        return self.in_sandbox(["/usr/bin/python3", "-c", code], timeout)

    def results(self, code):
        r = self.sandboxed(code)
        out = dict(line.split(" ", 1) for line in r.stdout.splitlines() if " " in line)
        self.assertTrue(out, r.stderr[-400:])
        return out

    # ---- writes ----
    def test_writes_outside_the_copy_are_blocked(self):
        outside = self.home / "escape.txt"
        (self.t.work / "link_out").symlink_to(self.home / "planted.txt")
        targets = {
            "home": str(outside),
            "dotdot": str(self.t.work / ".." / ".." / "escape2.txt"),
            "tmp": "/tmp/julius_escape_test.txt",
            "launchagent": str(self.home / "Library" / "LaunchAgents" / "evil.plist"),
            "real_project": str(self.b.proj / "app.py"),
            "symlink": str(self.t.work / "link_out"),
            "julius_state": str(self.home / ".julius" / "ui_token"),
        }
        code = "import os\n" + "".join(
            f"try:\n    open({path!r}, 'a').write('x'); print({name!r}, 'WROTE')\nexcept OSError: print({name!r}, 'blocked')\n"
            for name, path in targets.items()
        )
        out = self.results(code)
        for name in targets:
            with self.subTest(name):
                self.assertEqual(out.get(name), "blocked")
        self.assertFalse(outside.exists())
        self.assertFalse(Path("/tmp/julius_escape_test.txt").exists())
        self.assertEqual((self.b.proj / "app.py").read_text(), "def add(a, b):\n    return a - b\n")

    def link_target(self):
        return self.home / "Documents" / "taxes.pdf"

    def test_a_hard_link_to_a_file_outside_cannot_be_made(self):
        target = self.link_target()
        out = self.results(
            f"import os\ntry:\n    os.link({str(target)!r}, 'taxes_link'); print('link', 'MADE')\nexcept OSError: print('link', 'blocked')\n"
        )
        self.assertEqual(out["link"], "blocked")

    # ---- reads ----
    def test_secrets_and_personal_folders_cannot_be_read(self):
        code = "".join(
            f"try:\n    open({str(self.home / rel)!r}).read(); print({rel!r}.replace(' ', '_'), 'READ')\n"
            f"except OSError: print({rel!r}.replace(' ', '_'), 'blocked')\n"
            for rel in self.secrets
        )
        out = self.results(code)
        for rel in self.secrets:
            with self.subTest(rel):
                self.assertEqual(out.get(rel.replace(" ", "_")), "blocked")

    def test_other_coding_tasks_cannot_be_read_but_the_path_to_this_one_can(self):
        other = ca.CODE_HOME / "other-task" / "work" / "secret.py"
        other.parent.mkdir(parents=True)
        other.write_text("x")
        self.open_to_group(other)
        self.write_profile()
        out = self.results(
            f"import os\ntry:\n    open({str(other)!r}).read(); print('other', 'READ')\nexcept OSError: print('other', 'blocked')\n"
            f"print('parents', 'ok' if os.path.isdir({str(ca.CODE_HOME)!r}) and os.path.isdir({str(self.t.dir)!r}) else 'hidden')\n"
        )
        self.assertEqual(out["other"], "blocked")
        self.assertEqual(
            out["parents"], "ok"
        )  # Claude Code's Edit checks the folders above the file it writes

    def open_to_group(self, path):
        """As the coding user, file permissions must not be what stops a read: only the sandbox may (see below)."""

    def test_its_own_copy_can_be_read_and_written(self):
        out = self.results(
            "open('app.py').read(); print('read', 'ok')\nopen('new.py', 'w').write('x'); print('write', 'ok')\n"
        )
        self.assertEqual(out, {"read": "ok", "write": "ok"})

    # ---- network ----
    def test_only_the_proxy_port_is_reachable(self):
        targets = {
            "dns_udp": ("udp", "1.1.1.1", 53),
            "public_https": ("tcp", "1.1.1.1", 443),
            "ipv6": ("tcp6", "2606:4700:4700::1111", 443),
            "events_8765": ("tcp", "127.0.0.1", 8765),
            "ui_8775": ("tcp", "127.0.0.1", 8775),
            "panel_8785": ("tcp", "127.0.0.1", 8785),
            "ollama_11434": ("tcp", "127.0.0.1", 11434),
        }
        code = "import socket\n"
        for name, (kind, host, port) in targets.items():
            if kind == "udp":
                code += (
                    f"try:\n    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2); "
                    f"s.sendto(b'x', ({host!r}, {port})); print({name!r}, 'SENT')\nexcept OSError: print({name!r}, 'blocked')\n"
                )
            else:
                fam = "socket.AF_INET6" if kind == "tcp6" else "socket.AF_INET"
                code += (
                    f"try:\n    s = socket.socket({fam}); s.settimeout(2); s.connect(({host!r}, {port})); print({name!r}, 'OPEN')\n"
                    f"except OSError: print({name!r}, 'blocked')\n"
                )
        code += f"socket.create_connection(('127.0.0.1', {self.p.port}), 3).close(); print('proxy', 'open')\n"
        out = self.results(code)
        for name in targets:
            with self.subTest(name):
                self.assertEqual(out.get(name), "blocked")
        self.assertEqual(out["proxy"], "open")

    def test_the_proxy_lets_out_only_the_api_host(self):
        out = self.results(
            "import socket\n"
            f"def ask(t):\n    s = socket.create_connection(('127.0.0.1', {self.p.port}), 5); s.sendall(('CONNECT %s HTTP/1.1\\r\\n\\r\\n' % t).encode()); return s.recv(200).split(b'\\r\\n')[0].decode().replace(' ', '_')\n"
            "for t in ('collect.example:443', 'github.com:443', '127.0.0.1:8775', 'api.anthropic.com:80', 'api.anthropic.com.collect.example:443'):\n    print(t.replace(':', '_'), ask(t))\n"
        )
        for t, status in out.items():
            with self.subTest(t):
                self.assertIn("403", status)

    # ---- git ----
    def test_git_cannot_push_or_reach_the_real_project(self):
        real = str(self.b.proj)
        r = self.in_sandbox(
            [
                "/bin/sh",
                "-c",
                f"git remote add real {real} 2>&1; git push real HEAD:main 2>&1; echo rc=$?; "
                f"echo {real}/.git/objects > .git/objects/info/alternates; git fsck --connectivity-only >/dev/null 2>&1; echo alt=$?",
            ],
            timeout=60,
        )
        self.assertIn("rc=", r.stdout, r.stderr[-300:])  # the attempt really ran
        self.assertNotIn("rc=0", r.stdout, r.stdout)
        self.assertFalse(
            (self.b.proj / ".git").exists() and any((self.b.proj / ".git").glob("refs/heads/*"))
        )
        self.assertEqual((self.b.proj / "app.py").read_text(), "def add(a, b):\n    return a - b\n")


@unittest.skipUnless(SANDBOX, "needs macOS sandbox-exec")
class Processes(unittest.TestCase):
    """The real task runner, with a scripted stand-in for `claude` that tries to leave a process behind."""

    def setUp(self):
        from tests.test_code_agent import Base

        self.b = Base()
        self.b.setUp()
        self.addCleanup(self.b.tearDown)
        self.t = self.b.task()
        self.marker = (
            self.t.dir / "survivor.pid"
        )  # inside the task folder: the only place the sandbox lets it write
        self.agent = self.b.tmp / "fake_claude.sh"
        self.agent.write_text(
            "#!/bin/sh\n"
            # a child in a new session (outside the task's process group) that would keep running after the task ends
            f"/usr/bin/python3 -c 'import os,time,sys\nif os.fork()==0:\n os.setsid()\n open(sys.argv[1],\"w\").write(str(os.getpid()))\n time.sleep(120)' {self.marker} &\n"
            # wait until the child has written its marker (slow CI runners), so the reaper can't kill it before it starts
            f"for i in $(seq 1 100); do [ -s {self.marker} ] && break; sleep 0.1; done\n"
            'echo \'{"type":"result","subtype":"success","total_cost_usd":0.01,"result":"done"}\'\n'
        )
        self.agent.chmod(0o755)

    def alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def test_no_process_outlives_its_task(self):
        run = ca.start(self.t, api_key="sk-test", config=self.b.cfg, claude=str(self.agent))
        self.assertTrue(run.wait(60))
        deadline = time.time() + 10
        while not self.marker.exists() and time.time() < deadline:
            time.sleep(0.1)
        self.assertTrue(self.marker.exists(), "the stand-in never ran")
        pid = int(self.marker.read_text())
        self.addCleanup(lambda: self.alive(pid) and os.kill(pid, 9))
        time.sleep(1.5)
        self.assertFalse(
            self.alive(pid), "a process the agent started is still running after the task ended"
        )


# ---- the same attempts as the low-privilege coding user (S1), through the real sudo rule and code-run wrapper ----
RUN_AS = os.environ.get("JULIUS_TEST_RUN_AS", "")
NEEDS_USER = "needs the coding user and its sudo rule (JULIUS_TEST_RUN_AS=julius-code)"


def _real_home():
    import pwd

    return Path(pwd.getpwuid(os.getuid()).pw_dir)  # the suite points HOME at a temp folder


class _AsCodingUser:
    def use_coding_user(self):
        from tests.test_code_agent import Base

        saved = (ca.RUN_AS, ca.CODE_HOME, ca.PRIVATE_HOME)
        self.addCleanup(
            lambda: [
                setattr(ca, k, v) for k, v in zip(("RUN_AS", "CODE_HOME", "PRIVATE_HOME"), saved)
            ]
        )
        self.b = Base()
        self.b.setUp()
        self.addCleanup(self.b.tearDown)
        ca.configure({"run_as": RUN_AS})
        ca.PRIVATE_HOME = self.b.tmp / "private"


@unittest.skipUnless(SANDBOX and RUN_AS, NEEDS_USER)
class EscapeAsCodingUser(_AsCodingUser, Escape):
    """Every attempt runs as the coding user: sudo, code-run, then the task's own profile. The targets are your REAL
    files and folders, and they are only READ: nothing is ever written into your real home (unlike the fake-home suite)."""

    def setUp(self):
        self.use_coding_user()
        self.home = _real_home()
        candidates = [
            ".ssh/known_hosts",
            ".ssh/id_ed25519",
            ".ssh/id_rsa",
            ".zsh_history",
            ".zshrc",
            ".julius/ui_token",
            "dev/julius-assistant/.env",
            ".kaggle/access_token",
            "Library/Keychains/login.keychain-db",
            "Library/Cookies/Cookies.binarycookies",
            "Documents",
        ]
        self.secrets = {rel: "" for rel in candidates if (self.home / rel).exists()}
        self.assertGreaterEqual(len(self.secrets), 5, "too few real targets to mean something")
        self.p = ep.EgressProxy().start()
        self.addCleanup(self.p.stop)
        self.t = self.b.task()  # prepared in the shared task folder, as production will
        self.addCleanup(self.remove_tasks)
        self.profile = self.t.dir / "sandbox.sb"  # code-run accepts only the task's own profile
        self.write_profile()

    def remove_tasks(self):
        with contextlib.suppress(Exception):
            ca._reap(self.t)  # ends its processes and opens its files so they can be removed
        for d in (self.t.dir, ca.CODE_HOME / "other-task"):
            shutil.rmtree(d, ignore_errors=True)

    def write_profile(self):
        super().write_profile()
        os.chmod(self.profile, 0o640)

    def in_sandbox(self, argv, timeout=30):
        import json

        argv = ["/usr/bin/sandbox-exec", "-f", str(self.profile), *argv]
        return subprocess.run(
            [ca.SUDO, "-n", "-u", RUN_AS, ca.CODE_RUN, self.t.id],
            input=json.dumps({"argv": argv, "env": {"PATH": "/usr/bin:/bin"}}),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def link_target(self):
        return self.home / ".zshrc"

    def open_to_group(self, path):
        for p in (path, *path.parents):
            if p == ca.CODE_HOME:
                break
            os.chmod(p, 0o2770 if p.is_dir() else 0o660)

    def test_it_really_runs_as_the_coding_user(self):
        out = self.results("import os, pwd\nprint('user', pwd.getpwuid(os.getuid()).pw_name)\n")
        self.assertEqual(out["user"], RUN_AS)

    def test_the_ci_users_home_and_julius_records_are_out_of_reach(self):
        targets = {"ci_home": "/Users/julius-ci", "records": str(self.t.private / "task.json")}
        code = "import os\n" + "".join(
            f"try:\n    os.listdir({p!r}) if os.path.isdir({p!r}) else open({p!r}).read(); print({n!r}, 'READ')\n"
            f"except OSError: print({n!r}, 'blocked')\n"
            for n, p in targets.items()
        )
        out = self.results(code)
        for n in targets:
            with self.subTest(n):
                self.assertEqual(out.get(n), "blocked")

    def test_writes_outside_the_copy_are_blocked(self):
        planted = [self.home / "escape.txt", self.home / "Library" / "LaunchAgents" / "evil.plist"]
        self.addCleanup(lambda: [p.unlink() for p in planted if p.exists()])
        shared = (
            ca.CODE_HOME / "escape_shared.txt"
        )  # file permissions allow this one: only the sandbox stops it
        self.addCleanup(lambda: shared.unlink(missing_ok=True))
        tries = (
            ("home", planted[0]),
            ("launchagent", planted[1]),
            ("shared", shared),
            ("tmp", "/tmp/julius_escape_test.txt"),
        )
        out = self.results(
            "".join(
                f"try:\n    open({str(p)!r}, 'a').write('x'); print({n!r}, 'WROTE')\nexcept OSError: print({n!r}, 'blocked')\n"
                for n, p in tries
            )
        )
        for n, _ in tries:
            with self.subTest(n):
                self.assertEqual(out.get(n), "blocked")
        self.assertFalse(shared.exists())


@unittest.skipUnless(SANDBOX and RUN_AS, NEEDS_USER)
class ProcessesAsCodingUser(_AsCodingUser, unittest.TestCase):
    """The real runner as the coding user: a child in its own session, its marker and folder dropped, still ends."""

    def setUp(self):
        self.use_coding_user()
        self.agent = ca.SHARED_ROOT / "_escape_claude.sh"
        self.addCleanup(lambda: self.agent.unlink(missing_ok=True))

    def test_no_process_outlives_its_task(self):
        t = ca.create_task("leave a process running", str(self.b.proj), config=self.b.cfg)
        marker = t.dir / "survivor.pid"
        self.agent.write_text(
            "#!/bin/sh\n"
            f"(cd / && env -u JULIUS_CODE_TASK /usr/bin/python3 -c 'import os,time,sys\nif os.fork()==0:\n os.setsid()\n open(sys.argv[1],\"w\").write(str(os.getpid()))\n time.sleep(120)' {marker}) &\n"
            f"for i in $(seq 1 100); do [ -s {marker} ] && break; sleep 0.1; done\n"
            'echo \'{"type":"result","subtype":"success","total_cost_usd":0,"result":"done"}\'\n'
        )
        self.agent.chmod(0o750)
        run = ca.start(
            t, api_key="sk-test", config={**self.b.cfg, "run_as_claude": str(self.agent)}
        )
        self.assertTrue(run.wait(60))
        self.assertTrue(marker.exists(), "the stand-in never ran")
        pid = marker.read_text().strip()
        time.sleep(1.5)
        left = subprocess.run(
            ["/bin/ps", "-u", RUN_AS, "-o", "pid="], capture_output=True, text=True
        ).stdout.split()
        self.assertNotIn(pid, left, "a process the task started is still running")
        ca.discard(t)


@unittest.skipUnless(SANDBOX and RUN_AS, NEEDS_USER)
class MergeBackAsCodingUser(_AsCodingUser, unittest.TestCase):
    """Merge-back checks with Julius's records kept privately, as they are when tasks run as the coding user."""

    def setUp(self):
        self.use_coding_user()
        self.addCleanup(lambda: [shutil.rmtree(t.dir, ignore_errors=True) for t in ca.all_tasks()])

    def test_a_symlink_and_a_planted_git_hook_never_reach_the_project(self):
        t = self.b.task()
        (t.work / "key_copy.txt").symlink_to(_real_home() / ".zshrc")
        hook = t.work / ".git" / "hooks" / "post-merge"
        hook.write_text("#!/bin/sh\ntouch /tmp/julius_hook_ran\n")
        out = ca.merge(t, self.b.cfg)
        self.assertNotIn("key_copy.txt", out["added"])
        self.assertFalse((self.b.proj / "key_copy.txt").exists())
        self.assertFalse((self.b.proj / ".git").exists())
        self.assertTrue((t.private / "task.json").is_file())
        self.assertFalse((t.dir / "task.json").exists())


class MergeBack(unittest.TestCase):
    """What the agent leaves in its copy must not carry anything out on merge."""

    def setUp(self):
        from tests.test_code_agent import Base

        self.b = Base()
        self.b.setUp()
        self.addCleanup(self.b.tearDown)

    def test_a_symlink_in_the_copy_is_never_merged_as_its_target(self):
        t = self.b.task()
        secret = self.b.home / ".ssh" / "id_ed25519"
        secret.parent.mkdir(parents=True)
        secret.write_text("PRIVATE KEY MATERIAL")
        (t.work / "key_copy.txt").symlink_to(secret)
        out = ca.merge(t, self.b.cfg)
        self.assertNotIn("key_copy.txt", out["added"])
        self.assertFalse((self.b.proj / "key_copy.txt").exists())

    def test_paths_that_climb_out_or_hide_in_git_are_never_merged(self):
        t = self.b.task()
        for rel in (".git/hooks/post-merge", "sub/../../escape.py"):
            p = t.work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w") as f:
                f.write("x")
        ca.merge(t, self.b.cfg)
        self.assertFalse((self.b.proj / ".git" / "hooks" / "post-merge").exists())
        self.assertFalse((self.b.dev / "escape.py").exists())

    def test_a_case_only_rename_cannot_overwrite_a_file_changed_since(self):
        t = self.b.task()
        (self.b.proj / "app.py").write_text("changed by you since the copy\n")
        (t.work / "App.py").write_text("agent's version\n")
        plan = ca.merge_plan(t)
        out = ca.merge(t, self.b.cfg)
        self.assertEqual((self.b.proj / "app.py").read_text(), "changed by you since the copy\n")
        self.assertTrue(
            "App.py" in plan["conflicts"] or "App.py" not in out["added"] + out["modified"]
        )

    def test_a_huge_file_is_not_merged(self):
        t = self.b.task()
        with open(t.work / "big.bin", "wb") as f:
            f.seek(200 * 1024 * 1024)
            f.write(b"\0")
        plan = ca.merge_plan(t)
        self.assertNotIn("big.bin", plan["apply"])

    def tearDown(self):
        shutil.rmtree(self.b.tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
