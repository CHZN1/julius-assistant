"""Coding-agent sandbox escape tests (Security stage part 3).

Every attempt runs inside the real seatbelt profile (and, for processes, the real task runner with a scripted stand-in for
`claude`), against a fake home that mirrors the real one, so nothing here touches your own files. Each attempt must be
blocked; the merge-back checks must refuse what would carry something out of the copy."""

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
        (self.t.dir / "p.sb").write_text(
            ca.seatbelt_profile(self.t, home=self.home, proxy_port=self.p.port)
        )

    def sandboxed(self, code, timeout=30):
        return subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-f",
                str(self.t.dir / "p.sb"),
                "/usr/bin/python3",
                "-c",
                code,
            ],
            cwd=self.t.work,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

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

    def test_a_hard_link_to_a_file_outside_cannot_be_made(self):
        target = self.home / "Documents" / "taxes.pdf"
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
        (self.t.dir / "p.sb").write_text(
            ca.seatbelt_profile(self.t, home=self.home, proxy_port=self.p.port)
        )
        out = self.results(
            f"import os\ntry:\n    open({str(other)!r}).read(); print('other', 'READ')\nexcept OSError: print('other', 'blocked')\n"
            f"print('parents', 'ok' if os.path.isdir({str(ca.CODE_HOME)!r}) and os.path.isdir({str(self.t.dir)!r}) else 'hidden')\n"
        )
        self.assertEqual(out["other"], "blocked")
        self.assertEqual(
            out["parents"], "ok"
        )  # Claude Code's Edit checks the folders above the file it writes

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
        r = subprocess.run(
            [
                "/usr/bin/sandbox-exec",
                "-f",
                str(self.t.dir / "p.sb"),
                "/bin/sh",
                "-c",
                f"git remote add real {real} 2>&1; git push real HEAD:main 2>&1; echo rc=$?; "
                f"echo {real}/.git/objects > .git/objects/info/alternates; git fsck --connectivity-only >/dev/null 2>&1; echo alt=$?",
            ],
            cwd=self.t.work,
            capture_output=True,
            text=True,
            timeout=60,
        )
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
