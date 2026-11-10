"""Record the multi-turn voice eval set (Phase F2). Run it yourself in a terminal that has microphone permission.

    python evals/voice_record.py list                 the scenarios
    python evals/voice_record.py record               record all ten, in order (about 30 minutes)
    python evals/voice_record.py record s03 s07       only these (id prefixes)
    python evals/voice_record.py record --duplex d1 d2 d3   the duplex-audio live test (evals/duplex_scenarios.json, Julius started with JULIUS_DUPLEX=1)
    python evals/voice_record.py status               how many takes exist and how much disk they use
    python evals/voice_record.py delete-all           remove EVERY recording (asks you to type a confirmation)

How a take works: the always-on service is stopped for the duration (and started again afterwards, even if you press Ctrl+C), a private copy of Julius starts
with a throwaway memory, and this script shows one instruction at a time. It moves to the next instruction when Julius has replied. Enter = next now,
r = redo this conversation, q = quit. After the last turn it stops Julius and asks whether to keep the take.

Each take is written to voice_data/<date>/<scenario>__take<N>/: rec/mic.wav (all microphone audio, including while Julius spoke), rec/out.wav (what Julius played),
rec/events.jsonl (ASR results, drops, replies, timing), labels.jsonl (which instruction was on screen when), console.log, scenario.json.
voice_data/ is git-ignored and excluded from any release; delete-all removes it."""

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "voice_data"
SCENARIOS = Path(__file__).with_name("voice_scenarios.json")
DUPLEX_SCENARIOS = Path(__file__).with_name("duplex_scenarios.json")
SANDBOX = Path("~/dev/sandbox").expanduser()
DEV = Path("~/dev").expanduser()
STAGE = re.compile(r"\s*\[[^\]]*\]\s*")


def load_scenarios(path=SCENARIOS):
    return json.loads(Path(path).read_text())["scenarios"]


def reference_text(turn):
    """What the transcript should say: the `ref` if given, else `say` without [stage directions]."""
    return (turn.get("ref") or STAGE.sub(" ", turn["say"])).strip()


def preflight(scenario, sandbox=SANDBOX, dev=DEV):
    """Problems that would waste a take (missing sandbox files or project)."""
    problems = []
    for name in (scenario.get("pre") or {}).get("sandbox_files", []):
        if not (Path(sandbox) / name).exists():
            problems.append(
                f"{name} is not in {sandbox} (it may be in the trash: say 'undo', or recreate it)"
            )
    for name in (scenario.get("pre") or {}).get("projects", []):
        if not (Path(dev) / name).is_dir():
            problems.append(f"project {name} is not in {dev}")
    return problems


def select(scenarios, prefixes):
    if not prefixes:
        return scenarios
    chosen = [s for s in scenarios if any(s["id"].startswith(p) for p in prefixes)]
    if not chosen:
        raise SystemExit(f"no scenario matches {prefixes}")
    return chosen


def next_take(folder_for_day, scenario_id):
    n = 1
    while (folder_for_day / f"{scenario_id}__take{n}").exists():
        n += 1
    return folder_for_day / f"{scenario_id}__take{n}"


def disk_usage(root=DATA):
    files = [p for p in Path(root).rglob("*") if p.is_file()] if Path(root).exists() else []
    takes = {p.parent for p in files if p.name == "scenario.json"}
    return {
        "takes": len(takes),
        "files": len(files),
        "mb": round(sum(p.stat().st_size for p in files) / 1e6, 1),
    }


def delete_all(root=DATA, confirm=None):
    """Remove the whole voice_data folder. `confirm` must be the exact phrase."""
    if confirm != "delete all recordings":
        return False, "not deleted: the confirmation phrase did not match"
    if not Path(root).exists():
        return True, "nothing to delete"
    usage = disk_usage(root)
    shutil.rmtree(root)
    return True, f"deleted {usage['takes']} takes ({usage['mb']} MB)"


# ---------------- interactive part ----------------
def _count(path, kind):
    n = 0
    try:
        for line in Path(path).read_text().splitlines():
            if f'"kind": "{kind}"' in line:
                n += 1
    except OSError:
        pass
    return n


def _keys():
    q = queue.Queue()

    def reader():
        for line in sys.stdin:
            q.put(line.strip().lower())

    threading.Thread(target=reader, daemon=True).start()
    return q


def record_scenario(sc, take, keys, extra_env=None):
    """Returns 'keep' | 'redo' | 'quit'."""
    rec = take / "rec"
    take.mkdir(parents=True)
    (take / "scenario.json").write_text(json.dumps(sc, indent=1))
    memory = tempfile.mkdtemp(prefix="julius_voice_mem_")
    env = {
        **os.environ,
        "JULIUS_SESSION_RECORD_DIR": str(rec),
        "JULIUS_MEMORY_DIR": memory,
        "JULIUS_PROFILE": "cloud",
        **(extra_env or {}),
    }
    log = open(take / "console.log", "w")
    proc = subprocess.Popen(
        ["/bin/zsh", str(ROOT / "run_julius.sh")],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=ROOT,
        start_new_session=True,
    )
    events = rec / "events.jsonl"
    labels = open(take / "labels.jsonl", "a")
    result = "keep"
    try:
        print(
            f"\n=== {sc['id']}: {sc['title']} ===\n{sc.get('intro', '')}\nStarting Julius (about 20 seconds, stay quiet)...",
            flush=True,
        )
        t_end = time.time() + 180
        while (
            time.time() < t_end
            and proc.poll() is None
            and "julius ready" not in Path(take / "console.log").read_text(errors="replace")
        ):
            time.sleep(1)
        if proc.poll() is not None:
            print("Julius did not start; see", take / "console.log")
            return "quit"
        turns = sc["turns"]
        i = 0
        while i < len(turns):
            t = turns[i]
            group = [t]
            while (
                i + len(group) < len(turns) and turns[i + len(group)].get("when") == "during_reply"
            ):
                group.append(turns[i + len(group)])
            baseline = _count(events, "reply")
            while not keys.empty():
                keys.get_nowait()  # stray Enter presses must not skip this instruction
            print(f"\n--- turn {i + 1}/{len(turns)} ---")
            print(f"  SAY:  {t['say']}")
            if t.get("wait_note"):
                print(f"        ({t['wait_note']})")
            if t.get("when") == "pause":
                print("        (stop for about 2 seconds at [pause], then finish)")
            for g in group[1:]:
                print(f"  THEN, about 3 seconds into his answer, talk over him:  {g['say']}")
            if t.get("expect", {}).get("ignored"):
                print("        (he should stay silent; press Enter after about 8 seconds)")
            labels.write(
                json.dumps(
                    {
                        "t": time.time(),
                        "turn": i,
                        "say": t["say"],
                        "ref": reference_text(t),
                        "expect": t.get("expect"),
                        "when": t.get("when", "after_reply"),
                        "group": len(group),
                    }
                )
                + "\n"
            )
            labels.flush()
            deadline = time.time() + (10 if t.get("expect", {}).get("ignored") else 240)
            while time.time() < deadline:
                try:
                    k = keys.get(timeout=0.5)
                    if k == "r":
                        result = "redo"
                        raise StopIteration
                    if k == "q":
                        result = "quit"
                        raise StopIteration
                    break
                except queue.Empty:
                    pass
                if proc.poll() is not None:
                    print("Julius stopped unexpectedly.")
                    result = "quit"
                    raise StopIteration
                if _count(events, "reply") > baseline:
                    if (
                        len(group) > 1
                    ):  # the first reply is "Response stopped"; the instruction after it is answered by a second one: wait for that (up to 45 s)
                        t_wait = time.time() + 45
                        while (
                            _count(events, "reply") < baseline + 2
                            and time.time() < t_wait
                            and proc.poll() is None
                        ):
                            time.sleep(0.5)
                    time.sleep(1.5)
                    break
            i += len(group)
    except StopIteration:
        pass
    finally:
        labels.close()
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(40)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(10)
        log.close()
        from julius.audio.session_record import finalize

        finalize(rec)  # no-op when Julius already closed the recording itself
        shutil.rmtree(memory, ignore_errors=True)
    if result == "keep":
        while not keys.empty():
            keys.get_nowait()
        print("\nDone with this conversation. Enter = keep it, r = record it again, q = stop here.")
        k = keys.get()
        result = "redo" if k == "r" else "quit" if k == "q" else "keep"
    if result == "redo":
        shutil.rmtree(take, ignore_errors=True)
    return result


def record(prefixes, duplex=False):
    import julius.service.service as svc

    scenarios = select(load_scenarios(DUPLEX_SCENARIOS if duplex else SCENARIOS), prefixes)
    for sc in scenarios:
        for p in preflight(sc):
            print(f"cannot record {sc['id']}: {p}")
            return 1
    was_loaded = svc.status().get("loaded")
    if was_loaded:
        print("Stopping the always-on Julius for the recording (it starts again afterwards).")
        svc.off()
    try:
        keys = _keys()
        day = DATA / time.strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        print(
            f"{len(scenarios)} conversations. Julius will show one instruction at a time; speak naturally, at your normal distance from the mic."
        )
        idx = 0
        while idx < len(scenarios):
            sc = scenarios[idx]
            out = record_scenario(
                sc, next_take(day, sc["id"]), keys, {"JULIUS_DUPLEX": "1"} if duplex else None
            )
            if out == "quit":
                break
            if out == "keep":
                idx += 1
        left = [s for s in SANDBOX.iterdir()] if SANDBOX.exists() else []
        print("\nsandbox now has:", ", ".join(sorted(p.name for p in left)))
    finally:
        if was_loaded:
            print("Starting the always-on Julius again.")
            svc.on()
            time.sleep(3)
            svc.wake()
    print(
        "\nRecorded. Next: tell Claude the recording is done and it will build and run the replay scoring."
    )
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        for s in load_scenarios():
            print(f"{s['id']:28} {len(s['turns'])} turns  {s['title']}")
        return 0
    if cmd == "status":
        print(disk_usage())
        return 0
    if cmd == "delete-all":
        u = disk_usage()
        print(f"This deletes {u['takes']} recorded takes ({u['mb']} MB) in {DATA}.")
        ok, msg = delete_all(DATA, input("Type exactly: delete all recordings\n> ").strip())
        print(msg)
        return 0 if ok else 1
    if cmd == "record":
        rest = [a for a in argv[1:] if a != "--duplex"]
        return record(rest, duplex="--duplex" in argv[1:])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
