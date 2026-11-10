"""Multi-turn voice eval (Phase F2): scores the recorded conversations in voice_data/ per turn.

    python evals/voice_eval.py                 score the newest take of every scenario, print the table, write evals/results/voice_<stamp>.summary.json
    python evals/voice_eval.py --takes s03,s06 only those scenarios
    python evals/voice_eval.py --all-takes     every take, not just the newest
    python evals/voice_eval.py replay [--mode gate|none|aec] [--takes s06]   front-end replay (see below)

SCORE (free, instant) reads what happened in the live run, from each take's rec/events.jsonl, labels.jsonl and scenario.json:
  transcript   word error rate of what Whisper heard against what the script told the user to say (wake word and stage directions removed),
               and how many separate utterances it was split into (a pause cut in two shows up as extra utterances)
  handling     the turn's `expect` from evals/voice_scenarios.json: every `must` regex found in Julius's reply, no `must_not`, `ignored` means no reply at all,
               `interrupt` also needs a talk-over event
  latency      end of speech to first audio (the per-turn timing line); control answers (sleep, wake) have none

REPLAY (free except Whisper time) pushes the recorded microphone audio through the capture front end again, so a front-end change can be measured on the same
conversations: `gate` = today's behaviour (the microphone is shut while Julius plays and for a settle time afterwards), `none` = no gate (shows the raw echo problem),
`aec` = echo cancellation using out.wav as the reference (added in F3a). Each mode is segmented like the live loop (energy threshold, 0.6 s of silence) and transcribed;
it reports how much of the user's speech was recovered and how many segments were just Julius hearing himself."""

import argparse
import collections
import json
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "voice_data"
RESULTS = HERE / "results"


# ---------------- text helpers ----------------
def norm_words(text):
    t = re.sub(r"^\s*(?:hey |okay )?julius\b[\s,.:!?-]*", "", (text or "").lower())
    t = re.sub(
        r"^julius(?=[a-z])", "", t
    )  # "JuliusTrashReport.TXT": the wake word glued to the request
    t = t.replace("nevermind", "never mind").replace("okay", "ok")
    return re.sub(r"[^a-z0-9' ]+", " ", t.replace("’", "'")).split()


def wer(ref, hyp):
    """Word error rate, edit distance over reference length (1.0 when there is nothing to compare against)."""
    r, h = norm_words(ref), norm_words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


# ---------------- loading ----------------
def read_jsonl(path):
    out = []
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def find_takes(root=DATA, prefixes=None, all_takes=False):
    """{scenario_id: [take folders]} newest last; a take needs scenario.json and rec/events.jsonl."""
    found = collections.defaultdict(list)
    for p in sorted(Path(root).glob("*/*__take*")):
        if not (p / "scenario.json").exists() or not (p / "rec" / "events.jsonl").exists():
            continue
        sid = p.name.split("__take")[0]
        if prefixes and not any(sid.startswith(x) for x in prefixes):
            continue
        found[sid].append(p)
    ordered = {}
    for sid, takes in sorted(found.items()):
        takes.sort(key=lambda p: (p.parent.name, int(p.name.split("__take")[1])))
        ordered[sid] = takes if all_takes else takes[-1:]
    return ordered


# ---------------- scoring ----------------
def turn_windows(labels, end_t):
    """[(label, start, end)] where a label's window runs to the next label (or the end of the session)."""
    out = []
    for i, lab in enumerate(labels):
        out.append((lab, lab["t"], labels[i + 1]["t"] if i + 1 < len(labels) else end_t))
    return out


def latency_of(reply):
    """Seconds from the end of the user's speech to the first sound, from the per-turn timing line; None for control answers."""
    f = dict(kv.split("=", 1) for kv in (reply.get("timing") or "").split() if "=" in kv)
    for key in ("speech_end_to_audio", "first_audio"):
        try:
            return float(f[key])
        except (KeyError, ValueError):
            pass
    return None


def tool_text(take):
    """Everything the console shows except Julius's own spoken lines: tool results live here."""
    p = take / "console.log"
    if not p.exists():
        return ""
    return "\n".join(
        l
        for l in p.read_text(errors="replace").splitlines()
        if not l.lstrip().startswith("JULIUS:")
    )


def check_expect(expect, replies_text, had_turn, had_reply, talk_over, tools=""):
    """(ok, why) for one expected outcome. A `no_fabrication` turn may state numbers only when they appear in a tool result."""
    expect = expect or {}
    if expect.get("kind") == "no_fabrication" and had_reply:
        for rx in expect.get("must_not", []):
            for m in re.finditer(rx, replies_text, re.I):
                nums = re.findall(r"\d+(?::\d\d)?", m.group(0))
                if nums and all(n in tools for n in nums):
                    continue  # it came from a tool result this session
                return False, f"stated {m.group(0)!r} with no tool result behind it"
        return True, ""
    if expect.get("ignored"):
        return (not had_reply and not had_turn), (
            "answered although he should stay silent" if (had_reply or had_turn) else ""
        )
    if not had_reply:
        return False, "no reply"
    if expect.get("kind") == "interrupt" and not talk_over:
        return False, "never stopped when talked over"
    for rx in expect.get("must", []):
        if not re.search(rx, replies_text, re.I):
            return False, f"missing /{rx}/"
    for rx in expect.get("must_not", []):
        if re.search(rx, replies_text, re.I):
            return False, f"contains /{rx}/"
    return True, ""


def score_take(take):
    scenario = json.loads((take / "scenario.json").read_text())
    events = read_jsonl(take / "rec" / "events.jsonl")
    labels = read_jsonl(take / "labels.jsonl")
    end_t = (
        next(
            (e["t"] for e in events if e["kind"] == "session_end"), events[-1]["t"] if events else 0
        )
        + 1
    )
    turns = scenario["turns"]
    rows = []
    tools = tool_text(take)
    for lab, a, b in turn_windows(labels, end_t):
        i = lab["turn"]
        group = turns[i : i + lab.get("group", 1)]
        win = [e for e in events if a <= e["t"] < b]
        drops_echo = {
            e.get("text") for e in win if e["kind"] == "drop" and e.get("reason") == "echo"
        }
        asr = [e for e in win if e["kind"] == "asr" and e.get("text") not in drops_echo]
        refs = " ".join(re.sub(r"\s*\[[^\]]*\]\s*", " ", t.get("ref") or t["say"]) for t in group)
        hyp = " ".join(e["text"] for e in asr)
        replies = [e for e in win if e["kind"] == "reply" and e.get("text")]
        controls = [e for e in win if e["kind"] == "reply" and e.get("control")]
        text = " ".join(e["text"] for e in replies)
        talk_over = any(e["kind"] == "talk_over" for e in win)
        had_turn = any(e["kind"] == "turn" for e in win)
        ok_all, why_all = True, []
        for t in group:
            ok, why = check_expect(
                t.get("expect"), text, had_turn, bool(replies) or bool(controls), talk_over, tools
            )
            if not ok:
                ok_all = False
                why_all.append(why)
        lats = [latency_of(r) for r in replies if not r.get("control")]
        rows.append(
            {
                "scenario": scenario["id"],
                "turn": i,
                "say": refs,
                "heard": hyp,
                "wer": None
                if (group[0].get("expect") or {}).get("ignored")
                else round(wer(refs, hyp), 3),
                "utterances": len(asr),
                "expected_utterances": 0
                if (group[0].get("expect") or {}).get("ignored")
                else len(group),
                "handled_ok": ok_all,
                "why": "; ".join(why_all),
                "latency_s": next((x for x in lats if x is not None), None),
                "reply": text[:160],
                "echo_drops": len(drops_echo),
                "talk_over": talk_over,
            }
        )
    return rows


def summarize(rows):
    graded = [r for r in rows if r["wer"] is not None]
    lat = sorted(r["latency_s"] for r in rows if r["latency_s"] is not None)
    pct = lambda xs, q: round(xs[min(len(xs) - 1, int(q * len(xs)))], 2) if xs else None
    return {
        "turns": len(rows),
        "handled_ok": sum(r["handled_ok"] for r in rows),
        "handled_pct": round(100 * sum(r["handled_ok"] for r in rows) / max(1, len(rows)), 1),
        "mean_wer": round(statistics.mean(r["wer"] for r in graded), 3) if graded else None,
        "exact_transcripts": sum(r["wer"] == 0 for r in graded),
        "graded_turns": len(graded),
        "split_or_lost_turns": sum(r["utterances"] != r["expected_utterances"] for r in graded),
        "echo_drops": sum(r["echo_drops"] for r in rows),
        "latency_p50_s": pct(lat, 0.5),
        "latency_p95_s": pct(lat, 0.95),
        "latency_n": len(lat),
    }


def render(rows, summary):
    out = []
    for sid in dict.fromkeys(r["scenario"] for r in rows):
        out.append(f"\n{sid}")
        for r in (x for x in rows if x["scenario"] == sid):
            mark = "ok " if r["handled_ok"] else "BAD"
            w = "  -  " if r["wer"] is None else f"{r['wer']:.2f}"
            lat = "  -  " if r["latency_s"] is None else f"{r['latency_s']:4.1f}s"
            split = (
                ""
                if r["utterances"] == r["expected_utterances"]
                else f"  [{r['utterances']} utterances, expected {r['expected_utterances']}]"
            )
            out.append(
                f"  {r['turn'] + 1:>2} {mark} wer {w} lat {lat}  said: {r['say'][:46]!r}{split}"
            )
            if r["wer"]:
                out.append(f"        heard: {r['heard'][:70]!r}")
            if not r["handled_ok"]:
                out.append(f"        why: {r['why']}  reply: {r['reply'][:80]!r}")
    s = summary
    out += [
        "",
        f"turns {s['turns']}: handled correctly {s['handled_ok']} ({s['handled_pct']}%)",
        f"transcripts: mean WER {s['mean_wer']}, exact {s['exact_transcripts']}/{s['graded_turns']}, split or lost {s['split_or_lost_turns']}",
        f"latency (speech end to first audio): p50 {s['latency_p50_s']}s, p95 {s['latency_p95_s']}s over {s['latency_n']} turns; echo drops {s['echo_drops']}",
    ]
    return "\n".join(out)


def score_main(a):
    takes = find_takes(DATA, [x for x in a.takes.split(",") if x] or None, a.all_takes)
    if not takes:
        print("no recordings in voice_data/ (python evals/voice_record.py record)")
        return 1
    rows = []
    for sid, ts in takes.items():
        for t in ts:
            rows += score_take(t)
    summary = summarize(rows)
    print(render(rows, summary))
    RESULTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    prev = sorted(RESULTS.glob("voice_*.summary.json"))
    data = {
        "suite": "voice",
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "takes": {sid: [t.name for t in ts] for sid, ts in takes.items()},
        **summary,
        "ok_pct": summary["handled_pct"],
        "total_usd": 0.0,
    }
    if prev:
        old = json.loads(prev[-1].read_text())
        before = {(r["scenario"], r["turn"]) for r in old.get("rows", []) if r["handled_ok"]}
        data["regressions_vs_previous"] = {
            "newly_failing": [
                f"{r['scenario']}#{r['turn'] + 1}"
                for r in rows
                if not r["handled_ok"] and (r["scenario"], r["turn"]) in before
            ]
        }
    data["rows"] = rows
    (RESULTS / f"voice_{stamp}.summary.json").write_text(json.dumps(data, indent=1))
    return 0


# ---------------- replay ----------------
def replay_main(a):
    import replay_frontend

    return replay_frontend.main(a)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", nargs="?", default="score", choices=["score", "replay"])
    ap.add_argument("--takes", default="")
    ap.add_argument("--all-takes", action="store_true")
    ap.add_argument("--mode", default="gate", choices=["gate", "none", "aec"])
    a = ap.parse_args(argv)
    return score_main(a) if a.cmd == "score" else replay_main(a)


if __name__ == "__main__":
    sys.exit(main())
