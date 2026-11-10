import json
import tempfile
import unittest
from pathlib import Path

import julius.service.logscan as ls
import julius.service.soak as soak


def row(ts, msg, level="info"):
    return json.dumps({"ts": ts, "level": level, "event": "console", "msg": msg})


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))

    def test_categories_counts_and_severity(self):
        (self.d / "julius.jsonl").write_text(
            "\n".join(
                [
                    row("2026-11-06T10:00:00", "julius ready · cloud"),
                    row("2026-11-06T10:01:00", "[speech unavailable: [Errno 2] No such file"),
                    row("2026-11-06T10:02:00", "[speech unavailable: x]"),
                    row("2026-11-06T10:03:00", "[dropped as echo of own voice: 3 words, 1.2s]"),
                    row(
                        "2026-11-06T10:04:00",
                        "[ignored: nothing transcribed (no-speech 1.00, 0.8s)]",
                    ),
                    row("2026-11-06T10:05:00", "[stall detected — forcing tool use]"),
                    row("2026-11-06T10:06:00", "This process is not trusted!", "error"),
                    row("2026-11-06T10:07:00", "random chatter"),
                ]
            )
        )
        r = ls.scan(self.d, wake_log=self.d / "none")
        self.assertEqual(r["categories"]["tts_unavailable"]["count"], 2)
        self.assertEqual(r["categories"]["tts_unavailable"]["severity"], "problem")
        self.assertEqual(r["categories"]["echo_dropped"]["severity"], "notice")
        self.assertEqual(r["categories"]["service_start"]["count"], 1)
        self.assertIn("hotkey_unavailable", r["categories"])
        self.assertNotIn(None, r["categories"])
        self.assertIn("problems: 3", ls.render(r))

    def test_since_filters_and_rotated_files_are_read(self):
        (self.d / "julius.jsonl.1").write_text(
            row("2026-11-05T10:00:00", "[speech unavailable: old]")
        )
        (self.d / "julius.jsonl").write_text(
            row("2026-11-06T10:00:00", "[speech unavailable: new]")
        )
        self.assertEqual(
            ls.scan(self.d, wake_log=self.d / "n")["categories"]["tts_unavailable"]["count"], 2
        )
        self.assertEqual(
            ls.scan(self.d, since="2026-11-06", wake_log=self.d / "n")["categories"][
                "tts_unavailable"
            ]["count"],
            1,
        )

    def test_tracebacks_are_grouped_by_their_last_line(self):
        (self.d / "launchd.err").write_text(
            'Traceback (most recent call last):\n  File "a.py", line 3, in x\n    boom()\nValueError: bad 12\nnoise\nTraceback (most recent call last):\n  File "b.py", line 9\nValueError: bad 99\n'
        )
        self.assertEqual(ls.tracebacks(self.d), {"ValueError: bad N": 2})

    def test_wake_stats_flag_rejected_wake_like_text(self):
        w = self.d / "wake.jsonl"
        w.write_text(
            "\n".join(
                json.dumps(x)
                for x in [
                    {
                        "ts": "2026-11-06 10:00:00",
                        "event": "fire",
                        "accepted": True,
                        "heard": "Julius.",
                    },
                    {
                        "ts": "2026-11-06 10:01:00",
                        "event": "fire",
                        "accepted": False,
                        "whisper_s": 0.5,
                        "heard": "JuliusTrashReport.",
                    },
                    {
                        "ts": "2026-11-06 10:02:00",
                        "event": "fire",
                        "accepted": False,
                        "whisper_s": 0.25,
                        "heard": "Hmm.",
                    },
                ]
            )
        )
        s = ls.wake_stats(w)
        self.assertEqual(
            (s["fires"], s["accepted"], s["rejected"], s["reject_whisper_s"]), (3, 1, 2, 0.8)
        )
        self.assertEqual(len(s["wake_like_rejects"]), 1)


class LatencyTests(unittest.TestCase):
    def test_timing_lines_become_percentiles(self):
        rows = [
            {
                "msg": f"[timing: first_audio={1 + i / 10:.2f} speech_end_to_audio={2 + i / 10:.2f} asr=0.40 asr_logprob=-0.{i} no_speech=0.01 total=3.00 input=voice routed=brain error=no]"
            }
            for i in range(10)
        ]
        rows.append(
            {
                "msg": "[timing: first_audio=none speech_end_to_audio=na asr=na asr_logprob=-1.50 no_speech=na total=0.50 input=typed routed=shortcut error=yes]"
            }
        )
        line = ls.latency(rows)
        self.assertEqual(line["turns"], 11)
        self.assertEqual(line["errors"], 1)
        self.assertEqual(line["low_confidence_turns"], 1)
        self.assertEqual(line["speech_end_to_audio"]["n"], 10)
        self.assertAlmostEqual(line["speech_end_to_audio"]["p50"], 2.5, delta=0.11)


class SoakTests(unittest.TestCase):
    def rows(self, spec):
        return [
            {
                "ts": f"2026-11-06T10:{i:02d}:00",
                "t": 1000.0 + 60 * i,
                "pid": pid,
                "runs": str(runs),
                "state": "running",
                "rss_mb": rss,
                "cpu_pct": cpu,
            }
            for i, (pid, runs, rss, cpu) in enumerate(spec)
        ]

    def test_a_changed_pid_is_a_restart_and_memory_trend_ignores_it(self):
        spec = [(1, 1, 300 + i, 2.0) for i in range(8)] + [(2, 2, 250, 3.0) for _ in range(8)]
        s = soak.summarize(self.rows(spec))
        self.assertEqual(s["restarts_seen"], 1)
        self.assertEqual(s["run_counter_delta"], 1)
        self.assertGreater(s["rss_mb"]["trend_mb_per_hour"], 0)
        self.assertEqual(s["cpu_pct"]["max"], 3.0)

    def test_missing_service_counts_as_down(self):
        rows = self.rows([(1, 1, 300, 1.0)] * 5)
        rows[2]["pid"] = None
        self.assertEqual(soak.summarize(rows)["samples_without_service"], 1)

    def test_sampler_computes_cpu_between_samples(self):
        class P:
            def __init__(s, pid):
                pass

            def children(s, recursive=True):
                return []

            def cpu_times(s):
                return (P.t, 0.0)

            def memory_info(s):
                return type("M", (), {"rss": 200_000_000})()

            def num_threads(s):
                return 7

            def open_files(s):
                return [1, 2]

            def is_running(s):
                return True

        class PS:
            Process = P
            NoSuchProcess = AccessDenied = Exception

            @staticmethod
            def disk_usage(p):
                return type("D", (), {"free": 50e9})()

        smp = soak.Sampler(PS)
        P.t = 10.0
        a = smp.sample({"pid": "5", "state": "running", "runs": "3"})
        self.assertNotIn("cpu_pct", a)
        smp.last = (5, 10.0, smp.last[2] - 10)
        P.t = 12.0
        b = smp.sample({"pid": "5", "state": "running", "runs": "3"})
        self.assertAlmostEqual(b["cpu_pct"], 20.0, delta=2.0)
        self.assertEqual(b["rss_mb"], 200.0)


class StderrMarkTests(unittest.TestCase):
    def test_since_ignores_tracebacks_before_the_mark(self):
        import tempfile
        from pathlib import Path

        import julius.service.logscan as ls

        d = Path(tempfile.mkdtemp())
        (d / "launchd.err").write_text(
            "Traceback (most recent call last):\n  File x\nValueError: old\n"
        )
        self.assertEqual(sum(ls.tracebacks(d).values()), 1)
        ls.mark_stderr(d, now="2026-11-08T00:10:00")
        with (d / "launchd.err").open("a") as f:
            f.write("Traceback (most recent call last):\n  File y\nKeyError: new\n")
        self.assertEqual(sum(ls.tracebacks(d, "2026-11-08T00:00:00").values()), 1)
        self.assertEqual(list(ls.tracebacks(d, "2026-11-08T00:00:00"))[0], "KeyError: new")
        self.assertEqual(sum(ls.tracebacks(d).values()), 2, "no since: everything")


if __name__ == "__main__":
    unittest.main()
